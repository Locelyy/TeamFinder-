import os
import subprocess
import sys

DIR_PATH = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(DIR_PATH, "cert")
KEY_PATH = os.path.join(CERT_DIR, "key.pem")
CERT_PATH = os.path.join(CERT_DIR, "cert.pem")

def generate_certs():
    os.makedirs(CERT_DIR, exist_ok=True)
    
    print("[TLS] Generating self-signed certificates...")
    
    # Try using cryptography module first
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        import datetime
        
        # Generate private key
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )
        
        # Write private key
        with open(KEY_PATH, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))
            
        # Generate self-signed certificate
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "ID"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "East Java"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Surabaya"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ITS Computer Networks"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ])
        
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.datetime.utcnow()
        ).not_valid_after(
            # Our cert will be valid for 1 year
            datetime.datetime.utcnow() + datetime.timedelta(days=365)
        ).add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        ).sign(key, hashes.SHA256())
        
        # Write certificate
        with open(CERT_PATH, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
            
        print(f"[TLS] Certs successfully generated using python-cryptography:\n  Key: {KEY_PATH}\n  Cert: {CERT_PATH}")
        return True
        
    except ImportError:
        print("[TLS] 'cryptography' library not found. Attempting to fall back to 'openssl' command line utility...")
    except Exception as e:
        print(f"[TLS] Error generating via python-cryptography: {e}. Falling back...")

    # Fallback to openssl executable via shell
    try:
        cmd = [
            "openssl", "req", "-new", "-newkey", "rsa:2048", "-days", "365", 
            "-nodes", "-x509", 
            "-subj", "/C=ID/ST=EastJava/L=Surabaya/O=ITS/CN=localhost",
            "-keyout", KEY_PATH, "-out", CERT_PATH
        ]
        # In windows, we might need shell=True to find openssl in path
        subprocess.run(cmd, check=True, shell=sys.platform == 'win32')
        print(f"[TLS] Certs successfully generated using OpenSSL CLI:\n  Key: {KEY_PATH}\n  Cert: {CERT_PATH}")
        return True
    except Exception as e:
        print(f"[TLS] Fallback OpenSSL execution failed: {e}")
        print("[TLS] WARNING: TLS will not be functional unless cert.pem and key.pem are manually placed inside the 'cert/' directory.")
        return False

if __name__ == "__main__":
    generate_certs()
