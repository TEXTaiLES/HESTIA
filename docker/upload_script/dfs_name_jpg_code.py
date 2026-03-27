import os
import shutil
import requests

# --- API CONFIGURATION ---
API_KEY = "1f8XEe0OA1FqLAh17yO3cjq9zwuiIfLV"
API_BASE_URL = "http://api.textailes.athenarc.gr"
#API_KEY = "change-me-locally"
#API_BASE_URL = "http://127.0.0.1:5000"
ROBOT_IMAGES_ENDPOINT = "/robot-images"

def get_unique_path(target_dir, filename):
    
    name, extension = os.path.splitext(filename)
    counter = 1
    dest_path = os.path.join(target_dir, filename)
    
    while os.path.exists(dest_path):
        new_filename = f"{name}_{counter}{extension}"
        dest_path = os.path.join(target_dir, new_filename)
        counter += 1
        
    return dest_path

def find_and_copy_pattern(search_patterns, source_root, target_dir):
    """Search for files whose names match ANY of the given patterns (case-insensitive).
    search_patterns can be a string or a list of strings."""
    if isinstance(search_patterns, str):
        search_patterns = [search_patterns]
    search_patterns = [p.strip().lower() for p in search_patterns if p.strip()]

    stack = [source_root]

    print(f"--- Searching for files matching: {search_patterns} ---")

    while stack:
        current_dir = stack.pop()

        if os.path.abspath(current_dir) == os.path.abspath(target_dir):
            continue

        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    if entry.is_dir():
                        stack.append(entry.path)
                    elif entry.is_file():
                        name_lower = entry.name.lower()
                        if any(pat in name_lower for pat in search_patterns):
                            dest = get_unique_path(target_dir, entry.name)
                            if os.path.abspath(entry.path) != os.path.abspath(dest):
                                shutil.copy2(entry.path, dest)
                                print(f"   [Copied]: {os.path.basename(dest)} (from {entry.path})")
        except Exception as e:
            continue

def group_files_by_prefix(file_paths):
    """Group file paths by the stem before the last underscore.
    e.g. 'HIROX_3D_stereoscope_a.jpg' and 'HIROX_3D_stereoscope_b.jpg'
    both map to group key 'HIROX_3D_stereoscope'.
    Files with no underscore in the stem use the full stem as the key."""
    groups = {}
    for path in file_paths:
        stem = os.path.splitext(os.path.basename(path))[0]
        if '_' in stem:
            key = stem.rsplit('_', 1)[0]
        else:
            key = stem
        groups.setdefault(key, []).append(path)
    return groups


def upload_robot_images(file_paths, robot_pose=None, group_name=""):
    """POST all collected image files to the /robot-images endpoint in a single request."""
    url = API_BASE_URL.rstrip('/') + ROBOT_IMAGES_ENDPOINT
    headers = {"Authorization": f"Bearer {API_KEY}"}

    # Build metadata_map: {filename: {robot_pose: ...}}
    metadata_map = {
        os.path.basename(p): {"robot_pose": robot_pose}
        for p in file_paths
    }

    filenames = [os.path.basename(p) for p in file_paths]
    label = f" [{group_name}]" if group_name else ""
    print(f"\n   Uploading {len(file_paths)} file(s){label}: {', '.join(filenames)}", flush=True)

    open_files = []
    try:
        multipart = []
        for path in file_paths:
            fh = open(path, 'rb')
            open_files.append(fh)
            multipart.append(('file', (os.path.basename(path), fh)))

        response = requests.post(
            url,
            headers=headers,
            files=multipart,
            data={"metadata_map": __import__('json').dumps(metadata_map)},
            timeout=300
        )
        if response.status_code in (200, 201):
            data = response.json()
            print(f"   [ok] scan_id: {data.get('scan_id', '?')} | uploaded: {data.get('message', '')}")
        else:
            print(f"   [error] HTTP {response.status_code}: {response.text}")
    except requests.exceptions.Timeout:
        print("   [error] Request timed out.")
    except Exception as e:
        print(f"   [error] {e}")
    finally:
        for fh in open_files:
            fh.close()


def main():
    source_path = input("path: ").strip()
    search_query = input("name pattern(s) — comma-separated (e.g. .jpg,.png,.tif): ").strip()

    if not source_path or not search_query:
        print("error: path and name pattern are required.")
        return

    source_path = os.path.abspath(source_path)

    # Support comma or space-separated patterns
    import re
    search_patterns = [p for p in re.split(r'[,\s]+', search_query) if p]

    robot_pose_input = input("robot_pose (leave blank to skip): ").strip()
    robot_pose = robot_pose_input if robot_pose_input else None

    script_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(script_dir, "collected_assets")

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        print(f"Created destination directory: {target_dir}")

    if not os.path.exists(source_path):
        print("error: source path does not exist.")
        return

    find_and_copy_pattern(search_patterns, source_path, target_dir)
    print(f"\nFinished collecting files to: {target_dir}")

    # --- UPLOAD TO /robot-images ---
    all_files = [
        os.path.join(target_dir, f)
        for f in os.listdir(target_dir)
        if os.path.isfile(os.path.join(target_dir, f))
    ]

    if not all_files:
        print("[warn] collected_assets is empty — nothing to upload.")
    else:
        groups = group_files_by_prefix(all_files)
        print(f"\nStarting upload to API... ({len(all_files)} file(s) in {len(groups)} group(s))")
        for group_name, paths in sorted(groups.items()):
            upload_robot_images(paths, robot_pose=robot_pose, group_name=group_name)

    print("\nAll done!")

if __name__ == "__main__":
    main()