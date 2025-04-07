import os
import math
import csv
from io import BytesIO
from tqdm import tqdm
from PIL import Image

import gradio as gr
import folium
from folium import LatLngPopup

from owslib.wms import WebMapService
from geographiclib.geodesic import Geodesic

# -------------------------
# Helper Functions
# -------------------------


def get_tile_bounds(x, y, zoom, bounds):
    """
    Calculate the bounding box for a tile at (x, y) and zoom level.
    bounds: [minx, miny, maxx, maxy]
    """
    minx, miny, maxx, maxy = bounds
    num_tiles = 2**zoom
    tile_size_x = (maxx - minx) / num_tiles
    tile_size_y = (maxy - miny) / num_tiles

    tile_minx = minx + x * tile_size_x
    tile_maxx = minx + (x + 1) * tile_size_x
    tile_miny = maxy - (y + 1) * tile_size_y
    tile_maxy = maxy - y * tile_size_y
    return [tile_minx, tile_miny, tile_maxx, tile_maxy]


def calculate_area(bbox, radius):
    """
    Calculate the area (in square kilometers) of a bounding box using the Geodesic polygon method.
    """
    moon_geo = Geodesic(radius, 0.0)
    polygon = moon_geo.Polygon()
    # Order: (lat, lon)
    polygon.AddPoint(bbox[3], bbox[0])  # top left
    polygon.AddPoint(bbox[1], bbox[0])  # bottom left
    polygon.AddPoint(bbox[1], bbox[2])  # bottom right
    polygon.AddPoint(bbox[3], bbox[2])  # top right
    _, _, area = polygon.Compute()
    if math.isnan(area) or area <= 0:
        raise ValueError("Calculated area is not valid.")
    return area


def get_last_xy_from_csv(csv_path):
    last_x, last_y = 0, 0
    if os.path.exists(csv_path):
        with open(csv_path, mode="r") as csv_file:
            reader = csv.reader(csv_file)
            rows = list(reader)
            if len(rows) > 1:
                last_row = rows[-1]
                # Assuming 12th column is COL (index 11) and 11th column is ROW (index 10)
                last_x = int(last_row[11])
                last_y = int(last_row[10])
    return last_x, last_y


# -------------------------
# Gradio Callback Functions
# -------------------------


def load_layers(wms_url):
    """
    Connect to the provided WMS endpoint and return:
      - A list of available layer names (for the dropdown).
      - An HTML table that lists only the layer names.
    """
    try:
        wms = WebMapService(wms_url)
    except Exception as e:
        return gr.update(choices=[]), f"<p><b>Error connecting to WMS:</b> {e}</p>"

    layers = []
    html_table = "<table border='1' style='border-collapse: collapse; width:100%;'>"
    html_table += "<tr><th style='padding:8px;'>Layer Name</th></tr>"

    for layer in wms.contents:
        layers.append(layer)
        html_table += f"<tr><td style='padding:8px;'>{layer}</td></tr>"
    html_table += "</table>"

    return gr.update(choices=layers), html_table


def update_layer_params(wms_url, layer_name):
    """
    Updated to also return image format options from GetMap operation
    """
    try:
        wms = WebMapService(wms_url)
        layer_obj = wms[layer_name]
    except Exception as e:
        return gr.update(choices=[]), "", "", gr.update(choices=[])

    # Get CRS options
    crs_choices = layer_obj.crsOptions if layer_obj.crsOptions else []

    # Get image format options
    getmap_op = wms.getOperationByName("GetMap")
    format_options = getmap_op.formatOptions if getmap_op else []

    # Get layer details
    details = f"<h3>{layer_obj.title}</h3><p>{layer_obj.abstract}</p>"

    # Get bounding box
    bbox = layer_obj.boundingBox or [-180.0, -85.0511287798066, 180.0, 85.0511287798066]
    bbox_str = f"{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}"

    return (
        gr.update(choices=crs_choices),  # CRS dropdown
        bbox_str,  # Bounding box
        details,  # Layer info
        gr.update(
            choices=format_options, value=format_options[0] if format_options else None
        ),  # Image formats
    )


def preview_tiles(
    wms_url, layer_name, crs, bbox_str, width, height, image_format, zoom
):
    """
    Download a preview of 9 tiles (3×3 grid) and return a Folium mosaic map preview as HTML.
    This preview does not update the CSV file.
    """
    try:
        wms = WebMapService(wms_url)
    except Exception as e:
        return f"<p>Error connecting to WMS: {e}</p>"

    try:
        bbox_vals = [float(s.strip()) for s in bbox_str.split(",")]
        if len(bbox_vals) != 4:
            raise ValueError("Bounding box must have 4 comma-separated values")
    except Exception as e:
        return f"<p>Error parsing bounding box: {e}</p>"

    image_size = (int(width), int(height))
    bounds = bbox_vals
    moon_radius = 1737.4  # using Moon's radius as an example

    output_dir = f"./preview/{layer_name}_zoom_{zoom}_format_{image_format.split('/')[-1]}_projection_{crs}"
    os.makedirs(output_dir, exist_ok=True)

    # Calculate the center tile coordinates
    center_x = 1  # Center tile in a 3x3 grid
    center_y = 1  # Center tile in a 3x3 grid

    # Download a 3x3 grid (9 tiles)
    print(f"Using {image_format} format for tiles.")
    num_preview_tiles = 3
    for x in range(num_preview_tiles):
        for y in range(num_preview_tiles):
            tile_x = center_x + (x - 1)  # Adjust for center
            tile_y = center_y + (y - 1)  # Adjust for center
            tile_bbox = get_tile_bounds(tile_x, tile_y, int(zoom), bounds)
            try:
                img_resp = wms.getmap(
                    layers=[layer_name],
                    size=image_size,
                    srs=crs,
                    bbox=tile_bbox,
                    format=image_format,
                )
            except Exception as e:
                print(f"Error fetching preview tile {tile_x}, {tile_y}: {e}")
                continue

            try:
                tile_image = Image.open(BytesIO(img_resp.read()))
            except Exception as e:
                print(f"Error reading preview image for tile {tile_x}, {tile_y}: {e}")
                continue

            img_path = os.path.join(output_dir, f"tile_{tile_x}_{tile_y}.png")
            tile_image.save(img_path)

    # Create a Folium mosaic preview using the 3×3 grid
    mosaic = folium.Map(location=[0, 0], zoom_start=2, tiles=None)
    LatLngPopup().add_to(mosaic)
    for x in range(num_preview_tiles):
        for y in range(num_preview_tiles):
            tile_x = center_x + (x - 1)
            tile_y = center_y + (y - 1)
            tile_file = os.path.join(output_dir, f"tile_{tile_x}_{tile_y}.png")
            if os.path.exists(tile_file):
                tile_bbox = get_tile_bounds(tile_x, tile_y, int(zoom), bounds)
                folium.raster_layers.ImageOverlay(
                    image=tile_file,
                    bounds=[[tile_bbox[1], tile_bbox[0]], [tile_bbox[3], tile_bbox[2]]],
                    opacity=1,
                    interactive=True,
                    cross_origin=False,
                    zindex=1,
                ).add_to(mosaic)
    return mosaic._repr_html_()


def download_tiles(
    wms_url, layer_name, crs, bbox_str, width, height, image_format, zoom
):
    """
    Download all tiles, save them with CSV metadata, and return a Folium mosaic preview (3×3 grid)
    as confirmation.
    """
    try:
        wms = WebMapService(wms_url)
    except Exception as e:
        return f"<p>Error connecting to WMS: {e}</p>"

    try:
        bbox_vals = [float(s.strip()) for s in bbox_str.split(",")]
        if len(bbox_vals) != 4:
            raise ValueError("Bounding box must have 4 comma-separated values")
    except Exception as e:
        return f"<p>Error parsing bounding box: {e}</p>"

    image_size = (int(width), int(height))
    bounds = bbox_vals
    moon_radius = 1737.4
    num_tiles = 3**2  # 3x3 grid

    output_dir = (
        f"./datasets/{layer_name}_zoom_{zoom}_format_{image_format}_projection_{crs}"
    )
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, f"{layer_name}_zoom_{zoom}_tiles_info.csv")

    if not os.path.exists(csv_path):
        with open(csv_path, mode="w", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(
                [
                    "IMG_PATH",
                    "LL_LAT",
                    "LL_LON",
                    "UL_LAT",
                    "UL_LON",
                    "UR_LAT",
                    "UR_LON",
                    "LR_LAT",
                    "LR_LON",
                    "ZOOM",
                    "ROW",
                    "COL",
                    "SQ_KM_AREA",
                ]
            )

    last_x, last_y = get_last_xy_from_csv(csv_path)
    csv_file = open(csv_path, mode="a", newline="")
    csv_writer = csv.writer(csv_file)

    total_tiles = num_tiles
    center_x = 1  # Center tile in a 3x3 grid
    center_y = 1  # Center tile in a 3x3 grid
    start_index = last_x * 3 + last_y + 1
    print(f"Using{image_format} format for tiles.")

    for i in tqdm(range(start_index, total_tiles), desc="Downloading tiles"):
        x, y = divmod(i, 3)  # Adjust for 3x3 grid
        tile_x = center_x + (x - 1)
        tile_y = center_y + (y - 1)
        tile_bbox = get_tile_bounds(tile_x, tile_y, int(zoom), bounds)
        try:
            img_resp = wms.getmap(
                layers=[layer_name],
                size=image_size,
                srs=crs,
                bbox=tile_bbox,
                format=image_format,
            )
        except Exception as e:
            print(f"Error fetching tile {tile_x}, {tile_y}: {e}")
            continue

        try:
            tile_image = Image.open(BytesIO(img_resp.read()))
        except Exception as e:
            print(f"Error reading image for tile {tile_x}, {tile_y}: {e}")
            continue
        file_ext = image_format.split("/")[-1].split(";")[0].lower()

        img_path = os.path.join(output_dir, f"tile_{tile_x}_{tile_y}.{file_ext}")

        tile_image.save(img_path, format=file_ext.upper())
        try:
            area = calculate_area(tile_bbox, moon_radius)
        except Exception as e:
            area = None
            print(f"Error calculating area for tile {tile_x}, {tile_y}: {e}")

        csv_writer.writerow(
            [
                img_path,
                tile_bbox[1],
                tile_bbox[0],
                tile_bbox[3],
                tile_bbox[0],
                tile_bbox[3],
                tile_bbox[2],
                tile_bbox[1],
                tile_bbox[2],
                zoom,
                tile_y,
                tile_x,
                area,
            ]
        )
    csv_file.close()


# -------------------------
# Gradio Interface
# -------------------------

with gr.Blocks(
    css="""
    .gr-markdown { background-color: #f0f8ff; padding: 15px; border-radius: 8px; }
    .gr-button { background-color: #4CAF50; color: white; }
    .gr-gallery { margin-bottom: 20px; }
"""
) as demo:
    gr.Markdown("## **WMS Tiles Downloader and Mosaic Preview**")

    with gr.Row():
        wms_url_input = gr.Textbox(
            label="WMS Endpoint URL", value="http://webmap.lroc.asu.edu/"
        )
        load_layers_btn = gr.Button("Load Layers")

    with gr.Row():
        layers_dropdown = gr.Dropdown(label="Select Layer", choices=[])
        layers_table = gr.HTML(
            label="Layers Info", value="<p>Layer info will appear here.</p>"
        )

    with gr.Row():
        crs_dropdown = gr.Dropdown(label="CRS (We recommend EPSG:4326)", choices=[])
    bbox_input = gr.Textbox(
        label="Bounding Box (minx, miny, maxx, maxy)",
        placeholder="e.g. -180.0, -85.0511, 180.0, 85.0511",
    )

    with gr.Row():
        width_input = gr.Number(label="Image Width (px)", value=512)
        height_input = gr.Number(label="Image Height (px)", value=512)
        zoom_input = gr.Number(label="Zoom Level", value=5)
        image_format_input = gr.Dropdown(
            label="Image Format", choices=[], value=None, interactive=True
        )

    # Preview button comes first in the UI.
    preview_btn = gr.Button("Preview")
    preview_output = gr.HTML(label="Mosaic Preview")

    # Then the download button.
    download_btn = gr.Button("Download All Tiles")
    download_output = gr.HTML(label="Download Mosaic Preview")

    # Load layers and display an HTML table with layer names.
    load_layers_btn.click(
        load_layers, inputs=[wms_url_input], outputs=[layers_dropdown, layers_table]
    )

    # When a layer is selected, update CRS, bounding box, and show details (title + abstract).
    layers_dropdown.change(
        update_layer_params,
        inputs=[wms_url_input, layers_dropdown],
        outputs=[crs_dropdown, bbox_input, layers_table, image_format_input],
    )

    preview_btn.click(
        preview_tiles,
        inputs=[
            wms_url_input,
            layers_dropdown,
            crs_dropdown,
            bbox_input,
            width_input,
            height_input,
            image_format_input,
            zoom_input,
        ],
        outputs=[preview_output],
    )

    # Download all tiles and show a mosaic preview.
    download_btn.click(
        download_tiles,
        inputs=[
            wms_url_input,
            layers_dropdown,
            crs_dropdown,
            bbox_input,
            width_input,
            height_input,
            image_format_input,
            zoom_input,
        ],
        outputs=[download_output],
    )

if __name__ == "__main__":
    demo.launch()
