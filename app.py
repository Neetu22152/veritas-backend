import os
import json
import hashlib
import qrcode
from io import BytesIO
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from web3 import Web3
from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

load_dotenv()

app = Flask(__name__)
CORS(app)

# ── Blockchain connection ─────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(os.getenv("BLOCKCHAIN_URL")))
contract_address = os.getenv("CONTRACT_ADDRESS")
private_key = os.getenv("OWNER_PRIVATE_KEY")
owner_account = w3.eth.account.from_key(private_key)

# Load contract ABI
with open("VeritasCredential.json") as f:
    artifact = json.load(f)

contract = w3.eth.contract(
    address=Web3.to_checksum_address(contract_address),
    abi=artifact["abi"]
)

os.makedirs("certificates", exist_ok=True)

# ── Helper: hash a PDF file ───────────────────────────────────
def hash_pdf(pdf_bytes):
    """SHA-256 hash of PDF bytes → bytes32 for blockchain"""
    sha256_hash = hashlib.sha256(pdf_bytes).hexdigest()
    return "0x" + sha256_hash

# ── Helper: generate certificate PDF ─────────────────────────
def generate_certificate_pdf(student_name, institution, course, graduation_date, cert_id):
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                             rightMargin=inch, leftMargin=inch,
                             topMargin=inch, bottomMargin=inch)

    styles = getSampleStyleSheet()
    elements = []

    # Title style
    title_style = ParagraphStyle(
        'title', parent=styles['Title'],
        fontSize=28, textColor=colors.HexColor('#1a1a2e'),
        spaceAfter=6, alignment=TA_CENTER, fontName='Helvetica-Bold'
    )
    subtitle_style = ParagraphStyle(
        'subtitle', parent=styles['Normal'],
        fontSize=14, textColor=colors.HexColor('#185FA5'),
        spaceAfter=4, alignment=TA_CENTER
    )
    body_style = ParagraphStyle(
        'body', parent=styles['Normal'],
        fontSize=12, textColor=colors.HexColor('#333333'),
        spaceAfter=6, alignment=TA_CENTER
    )
    name_style = ParagraphStyle(
        'name', parent=styles['Normal'],
        fontSize=24, textColor=colors.HexColor('#185FA5'),
        spaceAfter=6, alignment=TA_CENTER, fontName='Helvetica-Bold'
    )
    small_style = ParagraphStyle(
        'small', parent=styles['Normal'],
        fontSize=9, textColor=colors.HexColor('#666666'),
        spaceAfter=4, alignment=TA_CENTER
    )

    # Certificate content
    elements.append(Spacer(1, 0.3*inch))
    elements.append(Paragraph("VERITAS", title_style))
    elements.append(Paragraph("Blockchain-Verified Academic Credential", subtitle_style))
    elements.append(Spacer(1, 0.3*inch))

    # Decorative line
    elements.append(Table(
        [['']], colWidths=[6*inch],
        style=TableStyle([('LINEABOVE', (0,0), (-1,-1), 2, colors.HexColor('#185FA5'))])
    ))
    elements.append(Spacer(1, 0.3*inch))

    elements.append(Paragraph("This is to certify that", body_style))
    elements.append(Spacer(1, 0.1*inch))
    elements.append(Paragraph(student_name, name_style))
    elements.append(Spacer(1, 0.1*inch))
    elements.append(Paragraph("has successfully completed", body_style))
    elements.append(Spacer(1, 0.1*inch))
    elements.append(Paragraph(f"<b>{course}</b>", body_style))
    elements.append(Spacer(1, 0.1*inch))
    elements.append(Paragraph(f"from <b>{institution}</b>", body_style))
    elements.append(Spacer(1, 0.1*inch))
    elements.append(Paragraph(f"Graduation Date: {graduation_date}", body_style))
    elements.append(Spacer(1, 0.3*inch))

    # Decorative line
    elements.append(Table(
        [['']], colWidths=[6*inch],
        style=TableStyle([('LINEABOVE', (0,0), (-1,-1), 2, colors.HexColor('#185FA5'))])
    ))
    elements.append(Spacer(1, 0.2*inch))

    # Certificate ID
    elements.append(Paragraph(f"Certificate ID: {cert_id}", small_style))
    elements.append(Paragraph("Verified on Ethereum Blockchain", small_style))

    # QR Code
    qr_data = f"Certificate ID: {cert_id} | Student: {student_name} | Course: {course}"
    qr = qrcode.make(qr_data)
    qr_buffer = BytesIO()
    qr.save(qr_buffer, format='PNG')
    qr_buffer.seek(0)

    elements.append(Spacer(1, 0.2*inch))
    elements.append(Image(qr_buffer, width=1.2*inch, height=1.2*inch))
    elements.append(Paragraph("Scan to verify authenticity", small_style))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()

# ── Route 1: Issue certificate ────────────────────────────────
@app.route('/issue', methods=['POST'])
def issue_certificate():
    try:
        data = request.json
        student_name    = data['studentName']
        institution     = data['institution']
        course          = data['course']
        graduation_date = data['graduationDate']
        ipfs_cid        = data.get('ipfsCID', 'Not uploaded')

        # Generate certificate ID
        cert_id = hashlib.md5(
            f"{student_name}{institution}{course}{graduation_date}".encode()
        ).hexdigest()[:12].upper()

        # Generate PDF
        pdf_bytes = generate_certificate_pdf(
            student_name, institution, course, graduation_date, cert_id
        )

        # Hash the PDF — this goes on the blockchain
        cert_hash = hash_pdf(pdf_bytes)
        cert_hash_bytes32 = bytes.fromhex(cert_hash[2:])

        # Save PDF locally
        pdf_path = f"certificates/{cert_id}.pdf"
        with open(pdf_path, 'wb') as f:
            f.write(pdf_bytes)

        # Send transaction to blockchain
        nonce = w3.eth.get_transaction_count(owner_account.address)
        tx = contract.functions.issueCertificate(
            cert_hash_bytes32,
            student_name,
            institution,
            course,
            graduation_date,
            ipfs_cid
        ).build_transaction({
            'from': owner_account.address,
            'nonce': nonce,
            'gas': 300000,
            'gasPrice': w3.eth.gas_price
        })

        signed_tx = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        return jsonify({
            'success': True,
            'certificateId': cert_id,
            'certificateHash': cert_hash,
            'transactionHash': tx_hash.hex(),
            'blockNumber': receipt['blockNumber'],
            'pdfUrl': f'/certificate/{cert_id}'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ── Route 2: Verify certificate ───────────────────────────────
@app.route('/verify', methods=['POST'])
def verify_certificate():
    try:
        # Accept either a hash directly or a PDF file upload
        if 'file' in request.files:
            pdf_file = request.files['file']
            pdf_bytes = pdf_file.read()
            cert_hash = hash_pdf(pdf_bytes)
        else:
            cert_hash = request.json['certificateHash']

        cert_hash_bytes32 = bytes.fromhex(cert_hash[2:])

        # Query the blockchain
        result = contract.functions.verifyCertificate(cert_hash_bytes32).call()

        if result[0]:  # is_valid
            return jsonify({
                'success': True,
                'isValid': True,
                'studentName':    result[1],
                'institution':    result[2],
                'course':         result[3],
                'graduationDate': result[4],
                'ipfsCID':        result[5],
                'issuedAt':       datetime.fromtimestamp(result[6]).strftime('%Y-%m-%d %H:%M:%S'),
                'certificateHash': cert_hash
            })
        else:
            return jsonify({
                'success': True,
                'isValid': False,
                'message': 'Certificate not found on blockchain — may be fake or tampered'
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ── Route 3: Download certificate PDF ────────────────────────
@app.route('/certificate/<cert_id>', methods=['GET'])
def download_certificate(cert_id):
    pdf_path = f"certificates/{cert_id}.pdf"
    if os.path.exists(pdf_path):
        return send_file(pdf_path, mimetype='application/pdf')
    return jsonify({'error': 'Certificate not found'}), 404

# ── Route 4: Health check ─────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'blockchain': w3.is_connected(),
        'blockNumber': w3.eth.block_number
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)