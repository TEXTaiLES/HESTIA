import os
import shutil
import requests

# --- API CONFIGURATION ---
#API_KEY = "1f8XEe0OA1FqLAh17yO3cjq9zwuiIfLV"
#API_BASE_URL = "http://api.textailes.athenarc.gr"
API_KEY = "change-me-locally"
API_BASE_URL = "http://127.0.0.1:5000"
RECONSTRUCTIONS_ENDPOINT = "/reconstructions"

def find_and_copy(filename, source_root, target_dir):
    """Search for a specific file (Texture or OBJ) using DFS and copy it.
    Returns the copied filename on success, None otherwise."""
    stack = [source_root]
    while stack:
        current_dir = stack.pop()
        
        # Avoid scanning the destination folder if it is inside the source path
        if os.path.abspath(current_dir) == os.path.abspath(target_dir):
            continue
            
        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if entry.is_dir():
                        stack.append(entry.path)
                    elif entry.is_file() and entry.name.lower() == filename.lower():
                        dest = os.path.join(target_dir, entry.name)
                        if os.path.abspath(entry.path) != os.path.abspath(dest):
                            shutil.copy2(entry.path, dest)
                            print(f"   Copied: {entry.name}")
                        return entry.name
        except Exception:
            continue
    return None

def process_mtl_assets(mtl_path, source_root, target_dir):
    """Read the MTL file and find required texture assets.
    Returns a list of copied filenames."""
    copied = []
    try:
        with open(mtl_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if line.strip().startswith('map_'):
                    parts = line.split()
                    if len(parts) > 1:
                        # Clean path in case the MTL was written on Windows
                        texture_name = os.path.basename(parts[-1].replace('\\', '/'))
                        result = find_and_copy(texture_name, source_root, target_dir)
                        if result:
                            copied.append(result)
    except Exception as e:
        print(f"   Error in MTL: {e}")
    return copied

def upload_file(file_path, scan_id):
    """POST a single file to /reconstructions."""
    url = API_BASE_URL.rstrip('/') + RECONSTRUCTIONS_ENDPOINT
    headers = {"Authorization": f"Bearer {API_KEY}"}
    filename = os.path.basename(file_path)

    print(f"   Uploading: {filename}", end='', flush=True)
    if filename.lower().endswith('.obj'):
        print(" (may take 2-3 min for GLB conversion...)", flush=True)
    else:
        print(flush=True)
    try:
        with open(file_path, 'rb') as fh:
            response = requests.post(
                url,
                headers=headers,
                files=[('file', (filename, fh))],
                data={"scan_id": scan_id},
                timeout=600  # 10 minutes — OBJ needs MinIO upload + GLB conversion
            )
        if response.status_code in (200, 201):
            print(f"   [ok] object_id: {response.json().get('object_id', '?')}")
        else:
            print(f"   [error] HTTP {response.status_code}: {response.text}")
    except requests.exceptions.Timeout:
        print(f"   [error] Request timed out — server is still processing, check logs")
    except Exception as e:
        print(f"   [error] {e}")


def main():
    # --- DEFINE YOUR SOURCE PATH HERE ---
    source_path = r"C:\Users\i.kechlimpari\Downloads\3D Models" 
    #source_path = r"C:\Users\i.kechlimpari\Downloads\7front75m8k"
    # Convert to absolute path for reliability
    source_path = os.path.abspath(source_path)
    
    # The target directory will be created in the same folder as the script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(script_dir, "input_dfs")

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        print(f"Created destination directory: {target_dir}")

    print(f"Starting collection from: {source_path}")

    # Maps each collected filename → its canonical scan_id (MTL stem)
    file_scan_id_map = {}
    
    stack = [source_path]
    while stack:
        current_dir = stack.pop()
        
        # Skip the destination folder to prevent infinite loops or SameFileErrors
        if os.path.abspath(current_dir) == os.path.abspath(target_dir):
            continue

        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if entry.is_dir():
                        stack.append(entry.path)
                    
                    elif entry.is_file() and entry.name.lower().endswith('.mtl'):
                        print(f"\nFound model: {entry.name}")
                        scan_id = os.path.splitext(entry.name)[0]  # e.g. "s1"
                        
                        # 1. Copy the MTL file itself
                        dest_mtl = os.path.join(target_dir, entry.name)
                        if os.path.abspath(entry.path) != os.path.abspath(dest_mtl):
                            shutil.copy2(entry.path, dest_mtl)
                        file_scan_id_map[entry.name] = scan_id
                        
                        # 2. Search and copy the corresponding OBJ file
                        obj_name = entry.name.lower().replace('.mtl', '.obj')
                        obj_result = find_and_copy(obj_name, source_path, target_dir)
                        if obj_result:
                            file_scan_id_map[obj_result] = scan_id
                        
                        # 3. Search for textures defined inside the MTL
                        texture_results = process_mtl_assets(dest_mtl, source_path, target_dir)
                        for tex in texture_results:
                            file_scan_id_map[tex] = scan_id
                        
        except PermissionError:
            print(f"Permission denied: {current_dir}")
            continue

    print(f"\nFinished! All files are collected in: {target_dir}")

    # --- UPLOAD TO /reconstructions ---
    print("\nStarting upload to API...")
    files = [f for f in os.listdir(target_dir) if os.path.isfile(os.path.join(target_dir, f))]
    if not files:
        print("[warn] input_dfs is empty — nothing to upload.")
    else:
        print(f"Found {len(files)} file(s) to upload.")
        for filename in sorted(files):
            # Use the mapped scan_id; fall back to file stem if not tracked
            scan_id = file_scan_id_map.get(filename, os.path.splitext(filename)[0])
            upload_file(os.path.join(target_dir, filename), scan_id)
    print("\nAll done!")

if __name__ == "__main__":
    main()