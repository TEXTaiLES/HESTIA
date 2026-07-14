from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional, Union
from urllib.parse import quote
from xml.dom import minidom
from xml.etree import ElementTree as elem_tree


JsonValue = Union[Dict[str, "JsonValue"], List["JsonValue"], str, int, float, bool, None]
JsonObject = Dict[str, JsonValue]

CONVERSIONS_DIR = Path(__file__).resolve().parent / "conversions"


def _load_conversion_json(file_name: str) -> JsonObject:
    """Load a conversion mapping or template file."""
    path = CONVERSIONS_DIR / file_name
    return json.loads(path.read_text(encoding="utf-8"))


TEXTAILES_XML_MAPPING = _load_conversion_json("textailes_xml_mapping.json")
TEXTAILES_JSON_TEMPLATE = _load_conversion_json("textailes_json_template.json")
HDTO_MAPPING = _load_conversion_json("textailes_hdto_mapping.json")

URI_TEMPLATES = HDTO_MAPPING["uri_templates"]
TEXTAILES_BASE_URI = URI_TEMPLATES["textailes_base"]
ECHOES_BASE_URI = URI_TEMPLATES["echoes_base"]
XML_TO_JSON_KEY_MAP = TEXTAILES_XML_MAPPING["xml_to_json_key_map"]
JSON_TO_XML_KEY_MAP = {value: key for key, value in XML_TO_JSON_KEY_MAP.items()}
JSON_TO_XML_KEY_MAP.update(TEXTAILES_XML_MAPPING.get("json_to_xml_key_map", {}))
METADATA_XML_SECTIONS = TEXTAILES_XML_MAPPING["metadata_sections"]


# ==============================================================================
# XML HELPERS
# ==============================================================================


def _parse_xml(metadata: str | bytes | elem_tree.Element | elem_tree.ElementTree) -> elem_tree.Element:
    """Return an XML root element from common XML input forms."""
    if isinstance(metadata, elem_tree.ElementTree):
        return metadata.getroot()
    if isinstance(metadata, elem_tree.Element):
        return metadata
    return elem_tree.fromstring(metadata)


def _serialize_xml(root: elem_tree.Element) -> str:
    """Serialize XML with stable indentation."""
    rough_xml = elem_tree.tostring(root, encoding="utf-8")
    parsed = minidom.parseString(rough_xml)
    return parsed.toprettyxml(indent="  ", encoding="UTF-8").decode("utf-8")


def _text(element: Optional[elem_tree.Element], default: str = "") -> str:
    """Return stripped element text or a default value."""
    if element is None or element.text is None:
        return default
    return element.text.strip()


def _add_text_element(
    parent: elem_tree.Element,
    tag: str,
    value: JsonValue = "",
    attributes: Optional[dict[str, str]] = None,
) -> elem_tree.Element:
    """Create a child element with optional text and attributes."""
    child = elem_tree.SubElement(parent, tag, attributes or {})
    if value is not None:
        child.text = _json_scalar_to_text(value)
    return child


def _add_repeated_text(
    parent: elem_tree.Element,
    tag: str,
    value: JsonValue,
    attributes: Optional[dict[str, str]] = None,
) -> None:
    """Add one element per value, or one empty element for missing values."""
    values = value if isinstance(value, list) else [value]
    if not values:
        _add_text_element(parent, tag, "", attributes=attributes)
        return

    for item in values:
        _add_text_element(parent, tag, item, attributes=attributes)


def _json_scalar_to_text(value: JsonValue) -> str:
    """Convert a JSON scalar to XML text."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _text_to_json_scalar(value: str) -> JsonValue:
    """Convert simple XML text values back to JSON scalars."""
    clean_value = value.strip()
    lower_value = clean_value.lower()
    if lower_value in {"true", "yes"}:
        return True
    if lower_value in {"false", "no"}:
        return False
    if clean_value == "":
        return ""

    try:
        if "." in clean_value:
            return float(clean_value)
        return int(clean_value)
    except ValueError:
        return clean_value


def _local_name(tag: str) -> str:
    """Return the local part of a possibly namespaced XML tag."""
    return tag.rsplit("}", maxsplit=1)[-1]


def _xml_key(json_key: str) -> str:
    """Return the XML tag used for a JSON key."""
    return JSON_TO_XML_KEY_MAP.get(json_key, json_key)


def _json_key(xml_key: str) -> str:
    """Return the JSON key used for an XML tag."""
    return XML_TO_JSON_KEY_MAP.get(xml_key, xml_key)


def _first_artefact(metadata: JsonObject) -> tuple[str, JsonObject]:
    """Return the first artefact key and object from the TEXTaiLES JSON."""
    if not metadata:
        raise ValueError("metadata must contain at least one artefact")

    name, artefact_data = next(iter(metadata.items()))
    if not isinstance(artefact_data, dict):
        raise ValueError("artefact metadata must be a JSON object")
    return name, artefact_data


def _normalize_scene_json(payload: JsonValue) -> JsonObject:
    """Reshape scene-export JSON into the TEXTaiLES artefact JSON shape.

    The Thoth client exports scenes as {"models": {<name>: {...}}, "scene_id": ...}
    with metadata nested under `metadata.attributes` and technological_analysis
    split into weave/warp/weft/decoration groups. This normalizer collapses
    those wrappers so the schema-driven converter can see every field.
    Idempotent: JSON already in the expected shape is returned unchanged.
    """
    if not isinstance(payload, dict) or not payload:
        return payload if isinstance(payload, dict) else {}

    models = payload.get("models")
    if isinstance(models, dict) and models:
        name, model = next(iter(models.items()))
        if isinstance(model, dict):
            artefact_key = _string_value(model.get("id")) or name
            payload = {artefact_key: model}

    _, artefact_data = next(iter(payload.items()))
    if not isinstance(artefact_data, dict):
        return payload

    metadata_section = artefact_data.get("metadata")
    if isinstance(metadata_section, dict) and isinstance(metadata_section.get("attributes"), dict):
        artefact_data["metadata"] = metadata_section["attributes"]

    metadata_section = artefact_data.get("metadata")
    if isinstance(metadata_section, dict):
        documentation = metadata_section.get("documentation")
        if isinstance(documentation, dict):
            technological = documentation.get("technological_analysis")
            if isinstance(technological, dict):
                structure = technological.get("structure")
                structure = structure if isinstance(structure, dict) else {}
                for group_key in ("weave_analysis", "warp_analysis", "weft_analysis"):
                    group = technological.pop(group_key, None)
                    if isinstance(group, dict):
                        structure.update(group)
                technological["structure"] = structure

    if isinstance(artefact_data.get("sensors"), list):
        artefact_data["sensors"] = {}

    return payload


# ==============================================================================
# TEXTAILES JSON <-> XML
# ==============================================================================


def textailes_json_to_xml(metadata: JsonObject | str) -> str:
    """Convert TEXTaiLES JSON metadata to TEXTaiLES XML.

    Args:
        metadata: TEXTaiLES artefact metadata as a dict or JSON string.

    Returns:
        XML document as a string.
    """
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    metadata = _normalize_scene_json(metadata)
    artefact_name, artefact_data = _first_artefact(metadata)
    artefact = artefact_data.get("artefact", {})
    metadata_section = artefact_data.get("metadata", {})
    annotations = artefact_data.get("annotations", {})
    sensors = artefact_data.get("sensors", {})

    if not isinstance(artefact, dict) or not isinstance(metadata_section, dict):
        raise ValueError("TEXTaiLES JSON must contain artefact and metadata objects")

    row_attributes = TEXTAILES_XML_MAPPING["row_attributes"]
    accession_number = _path_get(
        artefact_data,
        _string_value(row_attributes.get("item_source", "")),
        default=artefact_name,
    )
    sample_id = _path_get(
        artefact_data,
        _string_value(row_attributes.get("sample_id_source", "")),
        default=accession_number,
    )

    root = elem_tree.Element("root")
    row = elem_tree.SubElement(
        root,
        "row",
        {
            "item": _json_scalar_to_text(accession_number),
            "sample_id": _json_scalar_to_text(sample_id),
        },
    )

    _build_artefact_xml(row, artefact)
    _build_sensors_xml(row, sensors)
    _build_annotations_xml(row, annotations)
    _build_metadata_xml(row, metadata_section)

    return _serialize_xml(root)


def textailes_xml_to_json(metadata: str | bytes | elem_tree.Element | elem_tree.ElementTree) -> JsonObject:
    """Convert TEXTaiLES XML metadata to TEXTaiLES JSON.

    Args:
        metadata: TEXTaiLES XML document as a string, bytes, or ElementTree object.

    Returns:
        TEXTaiLES JSON object.
    """
    root = _parse_xml(metadata)
    item_row = root if root.tag == "row" else root.find("row")
    if item_row is None:
        raise ValueError("TEXTaiLES XML must contain a top-level row element")

    artefact_row = item_row.find("./row[@type='artefact']")
    metadata_row = item_row.find("./row[@type='metadata']")
    annotations_row = item_row.find("./row[@type='annotations']")
    sensors_row = item_row.find("./row[@type='sensors']")

    artefact = _parse_artefact_xml(artefact_row)
    metadata_json = _parse_metadata_xml(metadata_row)
    annotations = _parse_annotations_xml(annotations_row)
    sensors = _parse_sensors_xml(sensors_row)

    artefact_key = _artefact_key(artefact, item_row)
    return {
        artefact_key: {
            "artefact": artefact,
            "metadata": metadata_json,
            "annotations": annotations,
            "sensors": sensors,
        },
    }


def _build_artefact_xml(parent: elem_tree.Element, artefact: JsonObject) -> None:
    """Build the artefact XML section."""
    row = elem_tree.SubElement(parent, "row", {"type": "artefact"})
    _add_text_element(row, "title", artefact.get("title", ""), {"label": "Title of the artefact"})

    models = elem_tree.SubElement(row, "models")
    model = elem_tree.SubElement(models, "model")
    _add_text_element(model, "title", "", {"label": "Title of the model"})
    _add_text_element(model, "uri", artefact.get("gltf_file", ""), {"label": "URL to the 3D model"})
    _add_text_element(model, "description", "", {"label": "General model description"})

    _add_repeated_text(
        row,
        "description",
        artefact.get("description", ""),
        {"label": "General artefact description"},
    )
    _add_text_element(row, "owner", artefact.get("owner", ""), {"label": "Legal owner of the artefact"})
    _add_text_element(
        row,
        "inventory_number",
        artefact.get("inventory_number", ""),
        {"label": "Access number within the owner's inventory"},
    )
    _add_repeated_text(row, "keywords", artefact.get("keywords", []), {"label": "List of keywords"})
    _add_text_element(row, "copyright", artefact.get("copyright", ""), {"label": "Copyright"})


def _build_sensors_xml(parent: elem_tree.Element, sensors: JsonValue) -> None:
    """Build the sensors XML section."""
    row = elem_tree.SubElement(parent, "row", {"type": "sensors"})
    if not isinstance(sensors, dict) or not sensors:
        sensor = elem_tree.SubElement(row, "sensor")
        _add_text_element(sensor, "name")
        _add_text_element(sensor, "id")
        _add_text_element(sensor, "url")
        return

    for sensor_id, sensor_data in sensors.items():
        sensor = elem_tree.SubElement(row, "sensor")
        if isinstance(sensor_data, dict):
            _add_text_element(sensor, "name", sensor_data.get("name", ""))
            _add_text_element(sensor, "id", sensor_data.get("id", sensor_id))
            _add_text_element(sensor, "url", sensor_data.get("url", ""))


def _build_annotations_xml(parent: elem_tree.Element, annotations: JsonValue) -> None:
    """Build the annotations XML section."""
    row = elem_tree.SubElement(parent, "row", {"type": "annotations"})
    if not isinstance(annotations, dict):
        return

    _build_annotation_group(row, annotations, "selections", "selection")
    _build_annotation_group(row, annotations, "semantic_annotations", "semantic_annotation")
    _build_annotation_group(row, annotations, "measurements", "measurement")


def _build_annotation_group(
    parent: elem_tree.Element,
    annotations: dict[str, JsonValue],
    group_key: str,
    item_tag: str,
) -> None:
    """Build one annotation group."""
    group = elem_tree.SubElement(parent, group_key)
    values = annotations.get(group_key, {})
    if not isinstance(values, dict):
        return

    for annotation in values.values():
        if isinstance(annotation, dict):
            _build_annotation_item(group, item_tag, annotation)


def _build_annotation_item(parent: elem_tree.Element, item_tag: str, annotation: JsonObject) -> None:
    """Build one annotation item."""
    item = elem_tree.SubElement(parent, item_tag)
    for key in ("id", "name", "description"):
        _add_text_element(item, key, annotation.get(key, ""))

    for key in ("related_rgb_images", "related_multispectral_images"):
        container = elem_tree.SubElement(item, key)
        for image_url in _as_list(annotation.get(key, [])):
            _add_text_element(container, "image_url", image_url)

    related_artefacts = elem_tree.SubElement(item, "related_artefacts")
    for artefact in _as_list(annotation.get("related_artefacts", [])):
        if isinstance(artefact, dict):
            _add_text_element(related_artefacts, "artefact_uri", artefact.get("id", ""))
        else:
            _add_text_element(related_artefacts, "artefact_uri", artefact)

    annotation_details = elem_tree.SubElement(item, "annotation")
    details = annotation.get("annotation", {})
    if isinstance(details, dict):
        _build_annotation_details_xml(annotation_details, details)

    _add_text_element(item, "visible", annotation.get("visible", True))


def _build_annotation_details_xml(parent: elem_tree.Element, details: JsonObject) -> None:
    """Build annotation-specific details."""
    for key, value in details.items():
        tag = _xml_key(key)
        if isinstance(value, dict):
            child = elem_tree.SubElement(parent, tag)
            _build_annotation_details_xml(child, value)
        else:
            _add_text_element(parent, tag, value)


def _build_metadata_xml(parent: elem_tree.Element, metadata: JsonObject) -> None:
    """Build the metadata XML section."""
    row = elem_tree.SubElement(parent, "row", {"type": "metadata"})
    _build_metadata_node(row, metadata, METADATA_XML_SECTIONS)


def _build_metadata_node(parent: elem_tree.Element, data: JsonObject, schema: JsonValue) -> None:
    """Build metadata XML from the declared schema."""
    if isinstance(schema, list):
        for json_key in schema:
            _add_repeated_text(parent, _xml_key(json_key), data.get(json_key, ""))
        return

    if not isinstance(schema, dict):
        return

    for json_key, child_schema in schema.items():
        value = data.get(json_key, {})
        xml_tag = _xml_key(json_key)
        if child_schema is None:
            _add_repeated_text(parent, xml_tag, value)
            continue

        child = elem_tree.SubElement(parent, xml_tag)
        if isinstance(value, dict):
            _build_metadata_node(child, value, child_schema)
        elif isinstance(child_schema, list):
            _build_metadata_node(child, {}, child_schema)


def _parse_artefact_xml(row: Optional[elem_tree.Element]) -> JsonObject:
    """Parse the artefact XML section."""
    artefact: JsonObject = {
        "title": "",
        "gltf_file": "",
        "description": "",
        "owner": "",
        "keywords": [],
        "copyright": "",
    }
    if row is None:
        return artefact

    artefact["title"] = _text(row.find("title"))
    artefact["gltf_file"] = _text(row.find("./models/model/uri"))
    descriptions = [_text(element) for element in row.findall("description") if _text(element)]
    artefact["description"] = descriptions[0] if len(descriptions) == 1 else descriptions
    artefact["owner"] = _text(row.find("owner"))
    artefact["keywords"] = [_text(element) for element in row.findall("keywords") if _text(element)]
    artefact["copyright"] = _text(row.find("copyright"))

    inventory_number = _text(row.find("inventory_number"))
    if inventory_number:
        artefact["inventory_number"] = inventory_number

    return artefact


def _parse_metadata_xml(row: Optional[elem_tree.Element]) -> JsonObject:
    """Parse the metadata XML section."""
    if row is None:
        return {}
    return _parse_metadata_node(row, METADATA_XML_SECTIONS)


def _parse_metadata_node(parent: elem_tree.Element, schema: JsonValue) -> JsonObject:
    """Parse metadata XML according to the declared schema."""
    result: JsonObject = {}
    if isinstance(schema, list):
        for json_key in schema:
            xml_tag = _xml_key(json_key)
            elements = parent.findall(xml_tag)
            result[json_key] = _parse_repeated_elements(elements)
        return result

    if not isinstance(schema, dict):
        return result

    for json_key, child_schema in schema.items():
        xml_tag = _xml_key(json_key)
        if child_schema is None:
            result[json_key] = _parse_repeated_elements(parent.findall(xml_tag))
            continue

        child = parent.find(xml_tag)
        result[json_key] = _parse_metadata_node(child, child_schema) if child is not None else {}
    return result


def _parse_repeated_elements(elements: list[elem_tree.Element]) -> JsonValue:
    """Parse one or more scalar elements into JSON."""
    values = [_text_to_json_scalar(_text(element)) for element in elements]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return values


def _parse_annotations_xml(row: Optional[elem_tree.Element]) -> JsonObject:
    """Parse the annotations XML section."""
    annotations: JsonObject = {
        "selections": {},
        "measurements": {},
        "semantic_annotations": {},
    }
    if row is None:
        return annotations

    groups = (
        ("selections", "selection"),
        ("measurements", "measurement"),
        ("semantic_annotations", "semantic_annotation"),
    )
    for group_key, item_tag in groups:
        group = row.find(group_key)
        if group is None:
            continue

        parsed_group: JsonObject = {}
        for index, item in enumerate(group.findall(item_tag), start=1):
            parsed_item = _parse_annotation_item(item)
            item_id = str(parsed_item.get("id") or index)
            parsed_group[item_id] = parsed_item
        annotations[group_key] = parsed_group

    return annotations


def _parse_annotation_item(item: elem_tree.Element) -> JsonObject:
    """Parse one annotation item."""
    parsed: JsonObject = {
        "id": _text_to_json_scalar(_text(item.find("id"))),
        "name": _text(item.find("name")),
        "description": _text(item.find("description")),
        "related_rgb_images": _parse_image_urls(item.find("related_rgb_images")),
        "related_multispectral_images": _parse_image_urls(item.find("related_multispectral_images")),
        "related_artefacts": _parse_related_artefacts(item.find("related_artefacts")),
        "annotation": {},
        "visible": bool(_text_to_json_scalar(_text(item.find("visible"), default="true"))),
    }

    annotation = item.find("annotation")
    if annotation is not None:
        parsed["annotation"] = _parse_generic_xml_children(annotation)
    return parsed


def _parse_image_urls(parent: Optional[elem_tree.Element]) -> list[str]:
    """Parse image URLs from an annotation section."""
    if parent is None:
        return []
    return [_text(element) for element in parent.findall("image_url") if _text(element)]


def _parse_related_artefacts(parent: Optional[elem_tree.Element]) -> list[JsonObject]:
    """Parse related artefact references from an annotation section."""
    if parent is None:
        return []
    return [
        {
            "id": _text(element),
            "name": _text(element),
            "url": "",
        }
        for element in parent.findall("artefact_uri")
        if _text(element)
    ]


def _parse_generic_xml_children(parent: elem_tree.Element) -> JsonObject:
    """Parse arbitrary XML children into JSON."""
    parsed: JsonObject = {}
    for child in parent:
        key = _json_key(child.tag)
        if list(child):
            parsed[key] = _parse_generic_xml_children(child)
        else:
            parsed[key] = _text_to_json_scalar(_text(child))
    return parsed


def _parse_sensors_xml(row: Optional[elem_tree.Element]) -> JsonObject:
    """Parse the sensors XML section."""
    sensors: JsonObject = {}
    if row is None:
        return sensors

    for index, sensor in enumerate(row.findall("sensor"), start=1):
        sensor_id = _text(sensor.find("id"), default=str(index))
        if not sensor_id and not _text(sensor.find("name")) and not _text(sensor.find("url")):
            continue
        sensors[sensor_id] = {
            "name": _text(sensor.find("name")),
            "id": sensor_id,
            "url": _text(sensor.find("url")),
        }
    return sensors


# ==============================================================================
# TEXTAILES XML <-> HDTO RDF
# ==============================================================================


def textailes_to_hdto(metadata: str | bytes | elem_tree.Element | elem_tree.ElementTree) -> str:
    """Convert TEXTaiLES XML metadata to the mapped HDTO RDF subset.

    Args:
        metadata: TEXTaiLES XML document as a string, bytes, or ElementTree object.

    Returns:
        RDF/XML document as a string.
    """
    root = _parse_xml(metadata)
    textailes_json = textailes_xml_to_json(root)
    _, artefact_data = _first_artefact(textailes_json)

    if not isinstance(artefact_data.get("artefact", {}), dict):
        raise ValueError("TEXTaiLES XML could not be parsed into artefact metadata")

    _register_hdto_namespaces()

    rdf = elem_tree.Element(_qname("rdf:RDF"))
    entity_mapping = HDTO_MAPPING["entity"]
    about_source = entity_mapping["about_source"]
    about_value = _string_value(_path_get(artefact_data, about_source))
    object_uri = _format_uri(entity_mapping["about_template"], about_value)

    entity = elem_tree.SubElement(
        rdf,
        _qname(entity_mapping["class"]),
        {_qname("rdf:about"): object_uri},
    )

    for rule in HDTO_MAPPING["textailes_to_hdto"]:
        _apply_textailes_to_hdto_rule(entity, artefact_data, rule)

    return _serialize_xml(rdf)


def hdto_to_textailes(metadata: str | bytes | elem_tree.Element | elem_tree.ElementTree) -> str:
    """Convert the mapped HDTO RDF subset back to TEXTaiLES XML.

    Args:
        metadata: RDF/XML document as a string, bytes, or ElementTree object.

    Returns:
        TEXTaiLES XML document as a string.
    """
    root = _parse_xml(metadata)
    entity = _find_first_by_local_name(root, "HC3_Tangible_Heritage_Entity")
    if entity is None:
        raise ValueError("HDTO RDF must contain an HC3_Tangible_Heritage_Entity")

    metadata_json: JsonObject = {
        "converted.rdf": {
            "artefact": {
                "title": "",
                "gltf_file": "",
                "description": [],
                "owner": "",
                "keywords": [],
                "copyright": "",
            },
            "metadata": deepcopy(_empty_metadata_template()),
            "annotations": {
                "selections": {},
                "measurements": {},
                "semantic_annotations": {},
            },
            "sensors": {},
        },
    }

    _, artefact_data = _first_artefact(metadata_json)
    for rule in HDTO_MAPPING["hdto_to_textailes"]:
        _apply_hdto_to_textailes_rule(entity, artefact_data, rule)

    return textailes_json_to_xml(metadata_json)


def _apply_textailes_to_hdto_rule(
    entity: elem_tree.Element,
    artefact_data: JsonObject,
    rule: JsonObject,
) -> None:
    """Apply one TEXTaiLES-to-HDTO mapping rule."""
    kind = _string_value(rule.get("kind", ""))
    source = _string_value(rule.get("source", ""))
    value = _path_get(artefact_data, source)
    values = _as_list(value) if rule.get("repeat") else [value]

    for item in values:
        if kind == "note":
            _add_lang_text(entity, _qname("crm:P3_has_note"), item)
        elif kind == "identifier":
            _add_identifier(
                entity,
                item,
                _string_value(rule.get("identifier_type", "")),
                about_is_value=bool(rule.get("about_is_value", False)),
            )
        elif kind == "place":
            _add_place(entity, item)
        elif kind == "title":
            _add_title(entity, _string_value(item))
        elif kind == "site":
            _add_site(entity, item)
        elif kind == "type":
            _add_type(entity, item)
        elif kind == "actor":
            _add_actor(entity, item)
        elif kind == "label":
            _add_lang_text(entity, _qname("rdfs:label"), item)
        elif kind == "3d_model":
            _add_3d_model(entity, item)


def _apply_hdto_to_textailes_rule(
    entity: elem_tree.Element,
    artefact_data: JsonObject,
    rule: JsonObject,
) -> None:
    """Apply one HDTO-to-TEXTaiLES mapping rule."""
    kind = _string_value(rule.get("kind", ""))
    target = _string_value(rule.get("target", ""))
    value: JsonValue = ""

    if kind == "child_text":
        values = _find_child_texts(entity, _string_value(rule.get("source", "")))
        value = values[0] if rule.get("first") and values else values
    elif kind == "pref_labels":
        value = _find_pref_labels(entity)
    elif kind == "identifier":
        value = _extract_identifier(entity, _string_value(rule.get("identifier_type", "")))
    elif kind == "nested_label":
        value = _extract_nested_label(entity, _string_value(rule.get("property", "")))
    elif kind == "model_uri":
        value = _extract_model_uri(entity)
    elif kind == "note_containing":
        values = _path_get(artefact_data, _string_value(rule.get("source", "")), default=[])
        value = _first_matching_note(_as_text_list(values), _string_value(rule.get("text", "")))
    elif kind == "value_from_candidates":
        values = _path_get(artefact_data, _string_value(rule.get("source", "")), default=[])
        candidates = set(_as_text_list(rule.get("candidates", [])))
        value = _first_value(_as_text_list(values), candidates)
    elif kind == "value_containing":
        values = _path_get(artefact_data, _string_value(rule.get("source", "")), default=[])
        value = _first_value_containing(_as_text_list(values), _string_value(rule.get("text", "")))

    if target:
        _path_set(artefact_data, target, value)


def _add_identifier(
    parent: elem_tree.Element,
    value: JsonValue,
    identifier_type: str,
    about_is_value: bool = False,
) -> None:
    """Add a CIDOC identifier node."""
    clean_value = _string_value(value)
    if not clean_value:
        return

    wrapper = elem_tree.SubElement(parent, _qname("crm:P1_is_identified_by"))
    about = clean_value if about_is_value else _format_uri("identifier", clean_value)
    identifier = elem_tree.SubElement(
        wrapper,
        _qname("crm:E42_Identifier"),
        {_qname("rdf:about"): about},
    )
    type_wrapper = elem_tree.SubElement(identifier, _qname("crm:P2_has_type"))
    type_node = elem_tree.SubElement(
        type_wrapper,
        _qname("crm:E55_Type"),
        {_qname("rdf:about"): _format_uri("id_type", identifier_type)},
    )
    _add_lang_text(type_node, _qname("skos:prefLabel"), identifier_type)
    _add_lang_text(identifier, _qname("rdfs:label"), clean_value)


def _add_title(parent: elem_tree.Element, title: str) -> None:
    """Add a CIDOC linguistic appellation node."""
    if not title:
        return

    wrapper = elem_tree.SubElement(parent, _qname("crm:P1_is_identified_by"))
    appellation = elem_tree.SubElement(
        wrapper,
        _qname("crm:E33_E41_Linguistic_Appellation"),
        {_qname("rdf:about"): _format_uri("appellation", title)},
    )
    _add_lang_text(appellation, _qname("rdfs:label"), title)


def _add_place(parent: elem_tree.Element, value: JsonValue) -> None:
    """Add a CIDOC place node."""
    clean_value = _string_value(value)
    if not clean_value:
        return

    wrapper = elem_tree.SubElement(parent, _qname("crm:P53_has_former_or_current_location"))
    place = elem_tree.SubElement(
        wrapper,
        _qname("crm:E53_Place"),
        {_qname("rdf:about"): _format_uri("place", clean_value)},
    )
    _add_lang_text(place, _qname("rdfs:label"), clean_value)


def _add_site(parent: elem_tree.Element, value: JsonValue) -> None:
    """Add a CIDOC site node."""
    clean_value = _string_value(value)
    if not clean_value:
        return

    wrapper = elem_tree.SubElement(parent, _qname("crm:P46i_forms_part_of"))
    site = elem_tree.SubElement(
        wrapper,
        _qname("crm:E27_Site"),
        {_qname("rdf:about"): _format_uri("site", clean_value)},
    )
    _add_lang_text(site, _qname("rdfs:label"), clean_value)


def _add_actor(parent: elem_tree.Element, value: JsonValue) -> None:
    """Add a CIDOC actor node."""
    clean_value = _string_value(value)
    if not clean_value:
        return

    wrapper = elem_tree.SubElement(parent, _qname("crm:P49_has_former_or_current_keeper"))
    actor = elem_tree.SubElement(
        wrapper,
        _qname("crm:E39_Actor"),
        {_qname("rdf:about"): _format_uri("actor", clean_value)},
    )
    _add_lang_text(actor, _qname("rdfs:label"), clean_value)


def _add_type(parent: elem_tree.Element, value: JsonValue) -> None:
    """Add a CIDOC type node."""
    clean_value = _string_value(value)
    if not clean_value:
        return

    wrapper = elem_tree.SubElement(parent, _qname("crm:P2_has_type"))
    type_node = elem_tree.SubElement(
        wrapper,
        _qname("crm:E55_Type"),
        {_qname("rdf:about"): _format_uri("keyword", clean_value)},
    )
    _add_lang_text(type_node, _qname("skos:prefLabel"), clean_value)


def _add_3d_model(parent: elem_tree.Element, value: JsonValue) -> None:
    """Add a HDTO 3D model node."""
    model_uri = _string_value(value)
    if not model_uri:
        return

    model_property = elem_tree.SubElement(parent, _qname("hdto:HP21i_has_3D_representation"))
    elem_tree.SubElement(
        model_property,
        _qname("hdto:HC8_3D_Model"),
        {_qname("rdf:about"): model_uri},
    )


def _add_lang_text(parent: elem_tree.Element, tag: str, value: JsonValue) -> None:
    """Add a language-tagged text node."""
    clean_value = _string_value(value)
    if not clean_value:
        return

    child = elem_tree.SubElement(parent, tag, {_qname("xml:lang"): "en"})
    child.text = clean_value


def _register_hdto_namespaces() -> None:
    """Register namespaces declared in the HDTO mapping file."""
    namespaces = HDTO_MAPPING["namespaces"]
    if not isinstance(namespaces, dict):
        return

    for prefix, uri in namespaces.items():
        if prefix != "xml":
            elem_tree.register_namespace(prefix, uri)


def _qname(prefixed_name: str) -> str:
    """Return an ElementTree QName from a prefixed mapping name."""
    prefix, tag = prefixed_name.split(":", maxsplit=1)
    namespaces = HDTO_MAPPING["namespaces"]
    if not isinstance(namespaces, dict):
        raise ValueError("HDTO mapping namespaces must be an object")
    namespace = namespaces[prefix]
    return f"{{{namespace}}}{tag}"


def _format_uri(template_key: str, value: JsonValue) -> str:
    """Format a mapping URI template with a quoted value."""
    clean_value = _string_value(value)
    template = URI_TEMPLATES[template_key]
    return template.format(
        echoes_base=ECHOES_BASE_URI,
        textailes_base=TEXTAILES_BASE_URI,
        value=_quote_uri_part(clean_value),
    )


def _quote_uri_part(value: str) -> str:
    """Quote a value for use inside the example URI patterns."""
    return quote(value, safe="/:")


def _find_first_by_local_name(root: elem_tree.Element, local_name: str) -> Optional[elem_tree.Element]:
    """Find the first descendant with a given local name."""
    for element in root.iter():
        if _local_name(element.tag) == local_name:
            return element
    return None


def _find_child_texts(parent: elem_tree.Element, local_name: str) -> list[str]:
    """Find direct child texts with a given local name."""
    return [
        _text(child)
        for child in parent
        if _local_name(child.tag) == local_name and _text(child)
    ]


def _find_pref_labels(entity: elem_tree.Element) -> list[str]:
    """Extract keyword/type labels from the RDF entity."""
    labels: list[str] = []
    for type_wrapper in entity:
        if _local_name(type_wrapper.tag) != "P2_has_type":
            continue
        for pref_label in type_wrapper.iter():
            if _local_name(pref_label.tag) == "prefLabel" and _text(pref_label):
                labels.append(_text(pref_label))
    return labels


def _extract_identifier(entity: elem_tree.Element, identifier_type: str) -> str:
    """Extract an identifier label by its type label."""
    for wrapper in entity:
        if _local_name(wrapper.tag) != "P1_is_identified_by":
            continue

        type_labels = [
            _text(node)
            for node in wrapper.iter()
            if _local_name(node.tag) == "prefLabel"
        ]
        if identifier_type not in type_labels:
            continue

        for node in wrapper.iter():
            if _local_name(node.tag) == "label" and _text(node):
                return _text(node)
    return ""


def _extract_nested_label(entity: elem_tree.Element, property_name: str) -> str:
    """Extract the first nested rdfs:label below a named RDF property."""
    for wrapper in entity:
        if _local_name(wrapper.tag) != property_name:
            continue
        for node in wrapper.iter():
            if _local_name(node.tag) == "label" and _text(node):
                return _text(node)
    return ""


def _extract_model_uri(entity: elem_tree.Element) -> str:
    """Extract the 3D model URI from the RDF entity."""
    for wrapper in entity:
        if _local_name(wrapper.tag) != "HP21i_has_3D_representation":
            continue
        for node in wrapper:
            about = node.attrib.get(_qname("rdf:about"), "")
            if about:
                return about
    return ""


# ==============================================================================
# SHARED UTILITIES
# ==============================================================================


def _path_get(data: JsonValue, path: str, default: JsonValue = "") -> JsonValue:
    """Read a dotted path from a JSON-like object."""
    if not path:
        return default

    value = data
    for key in path.split("."):
        if not isinstance(value, dict):
            return default
        value = value.get(key, default)
    return value


def _path_set(data: JsonObject, path: str, value: JsonValue) -> None:
    """Set a dotted path on a JSON-like object."""
    keys = path.split(".")
    current: JsonValue = data
    for key in keys[:-1]:
        if not isinstance(current, dict):
            return
        next_value = current.get(key)
        if not isinstance(next_value, dict):
            next_value = {}
            current[key] = next_value
        current = next_value

    if isinstance(current, dict):
        current[keys[-1]] = value


def _as_list(value: JsonValue) -> list[JsonValue]:
    """Return a value as a list."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_text_list(value: JsonValue) -> list[str]:
    """Return a value as a list of non-empty strings."""
    return [_string_value(item) for item in _as_list(value) if _string_value(item)]


def _string_value(value: JsonValue) -> str:
    """Return a JSON value as a string."""
    if isinstance(value, list):
        return "; ".join(_string_value(item) for item in value if _string_value(item))
    return _json_scalar_to_text(value).strip()


def _artefact_key(artefact: JsonObject, item_row: elem_tree.Element) -> str:
    """Derive a stable artefact key for JSON output."""
    gltf_file = artefact.get("gltf_file")
    if isinstance(gltf_file, str) and gltf_file:
        return Path(gltf_file).name

    item = item_row.attrib.get("item")
    if item:
        return f"{item}.json"
    return "converted.json"


def _empty_metadata_template() -> JsonObject:
    """Return an empty TEXTaiLES metadata template."""
    return deepcopy(TEXTAILES_JSON_TEMPLATE)


def _first_matching_note(notes: list[str], text: str) -> str:
    """Return the first note containing text."""
    for note in notes:
        if text.lower() in note.lower():
            return note
    return ""


def _first_value(values: list[str], candidates: set[str]) -> str:
    """Return the first value found in a candidate set."""
    for value in values:
        if value in candidates:
            return value
    return ""


def _first_value_containing(values: list[str], text: str) -> str:
    """Return the first value containing text."""
    for value in values:
        if text.lower() in value.lower():
            return value
    return ""


# ==============================================================================
# TEST ENTRY POINT
# ==============================================================================


def main() -> None:
    """Run a local conversion smoke test using metadata_conversion files."""
    root_dir = Path(__file__).resolve().parents[2]
    conversion_dir = root_dir / "metadata_conversion"

    example_json = json.loads((conversion_dir / "example.json").read_text(encoding="utf-8"))
    example_xml = (conversion_dir / "benaki_temp_metadata.xml").read_text(encoding="utf-8")
    target_rdf = (conversion_dir / "target_schema.rdf").read_text(encoding="utf-8")

    xml_from_json = textailes_json_to_xml(example_json)
    json_from_xml = textailes_xml_to_json(example_xml)
    rdf_from_xml = textailes_to_hdto(example_xml)
    xml_from_rdf = hdto_to_textailes(target_rdf)
    round_trip_rdf = textailes_to_hdto(xml_from_rdf)

    print("json -> xml bytes:", len(xml_from_json.encode("utf-8")))
    print("xml -> json artefacts:", len(json_from_xml))
    print("xml -> rdf bytes:", len(rdf_from_xml.encode("utf-8")))
    print("rdf -> xml bytes:", len(xml_from_rdf.encode("utf-8")))
    print("rdf -> xml -> rdf bytes:", len(round_trip_rdf.encode("utf-8")))


if __name__ == "__main__":
    main()
