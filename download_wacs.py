import urllib.request
import json
import zipfile
import os

url = "https://api.github.com/repos/win-acme/win-acme/releases/latest"
req = urllib.request.Request(url)
req.add_header("User-Agent", "Mozilla/5.0")
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        
    download_url = None
    for asset in data.get("assets", []):
        if "trimmed" in asset.get("name", "") and "x64" in asset.get("name", "") and asset.get("name", "").endswith(".zip"):
            download_url = asset.get("browser_download_url")
            break

    if not download_url:
        print("Khong the tim thay link tai win-acme trimmed x64.")
        exit(1)

    print(f"Dang tai Win-Acme tu: {download_url}")
    zip_path, _ = urllib.request.urlretrieve(download_url, "wacs.zip")
    
    # Giải nén
    print("Dang giai nen...")
    os.makedirs("win-acme", exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall("win-acme")
    
    os.remove(zip_path)
    print("Tai va giai nen thanh cong!")
except Exception as e:
    print(f"Loi: {e}")
    exit(1)
