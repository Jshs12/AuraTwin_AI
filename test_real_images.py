import os
import requests
import time
from pathlib import Path

def test_images():
    image_dir = Path("data/test_images/new proj")
    if not image_dir.exists():
        print("Image directory not found.")
        return

    # Select up to 10 images
    valid_exts = {".jpg", ".jpeg", ".png", ".webp"}
    images = [f for f in image_dir.iterdir() if f.is_file() and f.suffix.lower() in valid_exts and not f.name.endswith(".part")]
    images = images[:10]

    if not images:
        print("No valid images found.")
        return
        
    print(f"Found {len(images)} images to test.")

    url = "http://localhost:8000/api/occupancy/detect"
    
    print("-" * 80)
    print(f"{'Filename':<30} | {'Count':<5} | {'Success':<8} | {'Model':<15} | {'Conf':<4} | {'Output'}")
    print("-" * 80)
    
    success_count = 0
    failure_count = 0
    
    for img_path in images:
        try:
            with open(img_path, "rb") as f:
                files = {"file": (img_path.name, f, "image/jpeg")}
                data = {"zone_id": "classroom_01"}
                response = requests.post(url, files=files, data=data)
                
            if response.status_code == 200:
                res = response.json()
                count = res["occupancy"]["people_count"]
                model = res["detection"]["model_name"]
                conf_used = "0.40"
                out_path = res["detection"]["annotated_image_path"] or "None"
                
                print(f"{img_path.name[:28]:<30} | {count:<5} | {'YES':<8} | {model[:15]:<15} | {conf_used:<4} | {Path(out_path).name}")
                success_count += 1
            else:
                print(f"{img_path.name[:28]:<30} | {'-':<5} | {'NO':<8} | {'-':<15} | {'-':<4} | {response.text}")
                failure_count += 1
        except Exception as e:
            print(f"{img_path.name[:28]:<30} | {'-':<5} | {'ERR':<8} | {'-':<15} | {'-':<4} | {str(e)}")
            failure_count += 1

    print("-" * 80)
    print(f"Processed: {len(images)}")
    print(f"Success: {success_count}")
    print(f"Failures: {failure_count}")

if __name__ == "__main__":
    test_images()
