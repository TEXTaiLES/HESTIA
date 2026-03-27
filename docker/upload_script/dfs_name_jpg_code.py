import os
import shutil

def get_unique_path(target_dir, filename):
    
    name, extension = os.path.splitext(filename)
    counter = 1
    dest_path = os.path.join(target_dir, filename)
    
    while os.path.exists(dest_path):
        new_filename = f"{name}_{counter}{extension}"
        dest_path = os.path.join(target_dir, new_filename)
        counter += 1
        
    return dest_path

def find_and_copy_pattern(search_pattern, source_root, target_dir):
   
    stack = [source_root]
    search_pattern = search_pattern.lower()
    
    print(f"--- Searching for files containing: '{search_pattern}' ---")
    
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
                        
                        if search_pattern in entry.name.lower():
                            
                            dest = get_unique_path(target_dir, entry.name)
                            
                            
                            if os.path.abspath(entry.path) != os.path.abspath(dest):
                                shutil.copy2(entry.path, dest)
                                print(f"   [Copied]: {os.path.basename(dest)} (from {entry.path})")
        except Exception as e:
           
            continue

def main():
   
    source_path = input("path ").strip()
    search_query = input("name ").strip()
    
    if not source_path or not search_query:
        print("error")
        return

    source_path = os.path.abspath(source_path)
    
   
    script_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(script_dir, "collected_assets")

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        print(f"Created destination directory: {target_dir}")

    
    if os.path.exists(source_path):
        find_and_copy_pattern(search_query, source_path, target_dir)
        print(f"\n end {target_dir}")
    else:
        print("error")
if __name__ == "__main__":
    main()