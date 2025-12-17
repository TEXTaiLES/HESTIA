from urllib.parse import urlparse
from datetime import datetime


def create_scene_object(urls, collaborative = True):
    """Create an ATON/THOTH compatible scene json object"""

    if isinstance(urls, str):
        urls = [urls]

    # Mesh names
    name_list = [
        urlparse(u).path.rstrip("/").split("/")[-1]
        for u in urls
    ]
    
    # Scene nodes
    nodes = {}
	
    for url in urls:
        name = urlparse(url).path.rstrip("/").split[-1]
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

    # Current datetime
    s = datetime.now().strftime("%Y%m%d%H%M")

    # Take the first url for the name
    name = urlparse(urls[0]).path.rstrip("/").split("/")[-1]

    return "${name}_${s}"