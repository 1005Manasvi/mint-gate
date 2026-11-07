"""Build the provenance test fixtures (deterministic content, committed to git).

  plain.png            no provenance evidence
  sd_watermark.png     invisible-watermark DWT-DCT 'StableDiffusionV1' (SD 1.x / 2.x reference scripts)
  sdxl_watermark.png   diffusers' SDXL 48-bit DWT-DCT watermark
  a1111.png            Automatic1111 `parameters` text chunk
  comfyui.png          ComfyUI `prompt` graph text chunk
  description.png      a human's description that merely mentions "Stable Diffusion" (must NOT count)
  xmp_ai.jpg           XMP Iptc4xmpExt:DigitalSourceType = trainedAlgorithmicMedia
  c2pa_ai.jpg          signed C2PA manifest, c2pa.created + trainedAlgorithmicMedia
  c2pa_capture.jpg     signed C2PA manifest, c2pa.created + digitalCapture
  c2pa_tampered.jpg    c2pa_capture.jpg with its pixels changed after signing (hash mismatch)
  test_ca.pem          the throw-away test CA (trust anchor for the "trusted" test)

The test CA / signing key are generated here and only the CA certificate is kept: nothing secret
is committed. Run from backend/:   python -m tests.fixtures.provenance.make_fixtures
"""

import datetime as dt
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, PngImagePlugin

HERE = Path(__file__).resolve().parent
ALG_OID_EMAIL = "1.3.6.1.5.5.7.3.4"


def art(seed=3, size=320) -> Image.Image:
    """A textured, painting-like test image (watermarks need texture + >= 256 px)."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size] / size
    base = np.stack([np.sin(6 * x + 2 * y), np.cos(5 * y - 3 * x), np.sin(4 * (x + y))], -1)
    noise = rng.normal(0, 0.15, (size // 8, size // 8, 3)).repeat(8, 0).repeat(8, 1)
    img = ((base * 0.4 + noise + 0.5).clip(0, 1) * 255).astype(np.uint8)
    return Image.fromarray(img)


def photo() -> Image.Image:
    """The photo fixture at 320 px: DWT-DCT watermarks only decode reliably on natural texture."""
    img = Image.open(HERE.parent / "photo.png").convert("RGB")
    s = 320 / min(img.size)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.BICUBIC)
    return img.crop((0, 0, img.width // 8 * 8, img.height // 8 * 8))  # generators output multiples of 8


def png_with_text(img, **chunks) -> bytes:
    info = PngImagePlugin.PngInfo()
    for k, v in chunks.items():
        info.add_text(k, v)
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def watermark(img, kind) -> bytes:
    from imwatermark import WatermarkEncoder

    from detection.provenance import SD1_MESSAGE, SDXL_BITS

    enc = WatermarkEncoder()
    if kind == "sd":
        enc.set_watermark("bytes", SD1_MESSAGE)
    else:
        enc.set_watermark("bits", SDXL_BITS)
    bgr = enc.encode(np.asarray(img)[:, :, ::-1].copy(), "dwtDct")
    buf = io.BytesIO()
    Image.fromarray(bgr[:, :, ::-1]).save(buf, format="PNG")
    return buf.getvalue()


XMP = """<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/"
 Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"/>
</rdf:RDF></x:xmpmeta><?xpacket end="w"?>"""


def certs():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID, ObjectIdentifier

    now = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "Mint-Gate test CA"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Mint-Gate tests"),
        ]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.COMMON_NAME, "Mint-Gate test signer"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Mint-Gate tests"),
                ]
            )
        )
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ObjectIdentifier(ALG_OID_EMAIL)]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM)  # noqa: E731
    key_pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return pem(leaf) + pem(ca), key_pem, pem(ca)


def c2pa_sign(jpeg: bytes, source_type: str, chain: bytes, key: bytes) -> bytes:
    import c2pa

    manifest = {
        "claim_generator_info": [{"name": "mint-gate-tests", "version": "1.0"}],
        "title": "fixture.jpg",
        "format": "image/jpeg",
        "assertions": [
            {
                "label": "c2pa.actions",
                "data": {
                    "actions": [
                        {
                            "action": "c2pa.created",
                            "digitalSourceType": f"http://cv.iptc.org/newscodes/digitalsourcetype/{source_type}",
                        }
                    ]
                },
            }
        ],
    }
    info = c2pa.C2paSignerInfo(c2pa.C2paSigningAlg.ES256, chain, key, None)
    signer = c2pa.Signer.from_info(info)
    builder = c2pa.Builder(json.dumps(manifest))
    out = io.BytesIO()
    builder.sign(signer, "image/jpeg", io.BytesIO(jpeg), out)
    return out.getvalue()


def jpeg(img, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92, **kw)
    return buf.getvalue()


def main():
    img = art()
    files = {
        "plain.png": png_with_text(img),
        "sd_watermark.png": watermark(photo(), "sd"),
        "sdxl_watermark.png": watermark(photo().transpose(Image.FLIP_LEFT_RIGHT), "sdxl"),
        "sdxl_watermark_jpeg.jpg": jpeg(Image.open(io.BytesIO(watermark(photo(), "sdxl")))),  # mark destroyed
        "photo_plain.png": png_with_text(photo()),
        "a1111.png": png_with_text(
            img,
            parameters="a cat\nSteps: 30, Sampler: DPM++ 2M Karras, CFG scale: 7, "
            "Seed: 1, Size: 512x512, Model hash: 6ce0161689",
        ),
        "comfyui.png": png_with_text(img, prompt=json.dumps({"3": {"class_type": "KSampler", "inputs": {"seed": 1}}})),
        "description.png": png_with_text(img, Description="Oil on canvas, painted by hand. Not Stable Diffusion!"),
        "xmp_ai.jpg": jpeg(img, xmp=XMP.encode()),
    }
    chain, key, ca = certs()
    files["c2pa_ai.jpg"] = c2pa_sign(jpeg(art(5)), "trainedAlgorithmicMedia", chain, key)
    cap = c2pa_sign(jpeg(art(6)), "digitalCapture", chain, key)
    files["c2pa_capture.jpg"] = cap
    # tamper: change bytes inside the entropy-coded image data (after the last APP segment), keep JPEG valid
    i = cap.rfind(b"\xff\xda") + 600
    files["c2pa_tampered.jpg"] = cap[:i] + bytes(b ^ 0x01 for b in cap[i : i + 40]) + cap[i + 40 :]
    files["test_ca.pem"] = ca
    for name, data in files.items():
        (HERE / name).write_bytes(data)
        print(f"{name}: {len(data)} bytes")


if __name__ == "__main__":
    main()
