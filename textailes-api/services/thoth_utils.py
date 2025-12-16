from urllib.parse import urlparse


def create_scene_object(url, collaborative = True):
    """Create an ATON/THOTH compatible scene json object"""

    if isinstance(url, str):
        url = [url]

    # Mesh names
    name_list = [
        urlparse(u).path.rstrip("/").split("/")[-1]
        for u in url
    ]
    
    # Scene nodes
    nodes = {}
	
    for url in url:
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
