from urllib.parse import urlparse
from datetime import datetime


def create_scene_object(urls, collaborative = True):
    """Create an ATON/THOTH compatible scene json object"""

    if isinstance(urls, str):
        urls = [urls]

    if not urls:
        raise ValueError("No urls provided")

    # Mesh names
    name_list = [
        get_name_from_url(u)
        for u in urls
    ]
    
    # Scene nodes
    nodes = {}
	
    for url in urls:
        name = get_name_from_url(url)
        nodes.setdefault(name, {"urls": []})["urls"].append(url)

    # Rest of the logic is handled by THOTH
    return {
		"status"       : "complete",
		"collaborative": collaborative,
		"scenegraph"   : {
			"nodes": nodes,
			"edges": {
				"." : name_list
            }, 
        }
    }


def create_scene_name(urls):
    """Create scene name"""

    if isinstance(urls, str):
        urls = [urls]

    if not urls:
        raise ValueError("No urls provided")
    
    # Current datetime
    s = datetime.now().strftime("%Y%m%d%H%M")

    # Take the first url for the name
    name = get_name_from_url(urls[0])

    return str(f"${name}_${s}")


def get_name_from_url(url):
    """Get model name from model url"""
    return str(urlparse(url).path.rstrip("/").split("/")[-1])