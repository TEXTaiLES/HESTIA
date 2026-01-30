# HESTIA Data Pipeline

This project provides a Dockerized environment for a multi-modal data pipeline (HESTIA). It features an upload API with Kafka event streaming, Postgres persistence, MinIO (S3-compatible) storage, a Directus-based archive portal, and Swagger UI for documentation. The system handles binary files (Artifacts), structured data (Sensor Readings), and 3D textile models (GLB files).

## Overview

The system consists of the following components:

### Data Ingestion Layer
- **Flask API**: A RESTful API built with Flask to handle uploads and queries. It uses an **API Key** for authentication.
- **Kafka**: Streams metadata and events (`artifacts`, `sensor_readings`, `artifact_uploaded`).
- **Kafka Connect**: Automatically syncs Kafka messages to Postgres (via JDBC sink).
- **Artifact Consumer**: An internal service that reacts to upload events (e.g., to update timestamps or trigger processing).

### Storage Layer
- **Postgres**: Stores structured metadata, sensor readings, and archive collections.
- **MinIO**: Stores large binary files (images, logs, GLB models, etc.) in an S3-compatible bucket.

### Archive Portal Layer
- **Directus CMS**: Backend content management system for database administration and user management
- **Custom Portal Frontend**: 
  - Public-facing archive website with home, collections, and tools pages
  - Server-side rendered HTML with custom Node.js endpoints
  - 3D artifact viewer using ATON framework for GLB models
  - Authenticated artifact management (view, create, edit)
- **Custom Directus Extensions**:
  - **Endpoints**: Archive routes (home, collections, artifact pages, toolbox)
  - **Interfaces**: Vue.js components for thumbnail generation within Directus admin
  - **Python Integration**: Headless 3D rendering (pyrender + trimesh) for GLB thumbnail generation


![Architecture](textailesdocker/image.png)

![Workflow](textailesdocker/image-1.png)

## Prerequisites

- [Docker](https://www.docker.com/get-started "null")
- [Docker Compose](https://docs.docker.com/compose/ "null")

## Project Structure

```
textailes-api/
├── api.py                  # Entry point for the Flask API
├── utils.py                # Shared utilities (DB connection, Auth, Kafka wrappers)
├── resources/              # API Resource definitions
│   ├── artifact.py         # Logic for Artifacts (Files)
│   └── sensor.py           # Logic for Sensor Readings (Data)
├── artifact_consumer.py    # Internal Kafka consumer service
├── Dockerfile.api          # Dockerfile for the API service
├── Dockerfile.consumer     # Dockerfile for the consumer service
├── requirements.txt        # Python dependencies
├── static/swagger.json     # Swagger UI definition

textailesdocker/
├── connectors/             # Kafka Connect configuration files
│   ├── postgres-sink.json  # Config for Artifacts table
│   └── sensor-sink.json    # Config for Sensor Readings table
├── directus_extensions/    # Directus custom extensions
│   ├── endpoints/          # Custom API endpoints
│   │   └── archive/        # Archive-specific endpoints
│   │       ├── src/
│   │       │   ├── routes/         # Route handlers for portal pages
│   │       │   │   ├── home.js             # Landing page with archive stats
│   │       │   │   ├── collections.js      # Browse artifacts by use case
│   │       │   │   ├── artefact.js         # Single artifact detail page
│   │       │   │   ├── add-artefact.js     # Artifact creation form
│   │       │   │   ├── toolbox.js          # TEXTaiLES tools directory
│   │       │   │   ├── thumbnail.js        # GLB thumbnail generation endpoint
│   │       │   │   ├── assets.js           # Static file serving
│   │       │   │   └── user.js             # Login/logout handlers
│   │       │   ├── templates/      # HTML template functions
│   │       │   └── utils/          # Helper utilities and constants
│   │       └── scripts/
│   │           └── generate_thumbnail.py  # Python 3D rendering script
│   └── interfaces/         # Custom UI components
│       └── generate-thumbnail/  # Button interface for manual thumbnail generation
│           └── src/
│               ├── interface.vue    # Vue component with view selector
│               └── index.js         # Interface registration
├── docker-compose.yml      # Service orchestration
├── Dockerfile.directus     # Custom Directus image with Python rendering
├── .env.example            # Template for environment variables
```

## Quick Start & Setup

### 1. Clone the Repository

```
git clone https://github.com/TEXTaiLES/HESTIA.git
cd HESTIA
```

### 2. Configure Security (.env)

**Important:** You must set up your local secrets before starting the app.
1. Navigate to the docker directory:

   ```
   cd textailesdocker
   ```
2. Copy the example environment file:

   ```
   cp .env.example .env
   ```
3. (Optional) Edit `.env` to change your `API_SECRET_KEY` or database passwords. *Default API Key:* `change-me-locally`

### 3. Start the Services

```
sudo docker-compose up --build -d
```

*Wait about 60 seconds for Kafka and Kafka Connect to fully initialize.*

### 4. Register Kafka Connectors

You must register the bridges that connect Kafka to Postgres. Run these commands from the `textailesdocker` directory:
- **Register Artifact Connector:**

  ```
  curl -X POST -H "Content-Type: application/json" \
    --data @connectors/postgres-sink.json \
    http://localhost:8083/connectors
  ```
- **Register Sensor Connector:**

  ```
  curl -X POST -H "Content-Type: application/json" \
    --data @connectors/sensor-sink.json \
    http://localhost:8083/connectors
  ```

## API Reference

**Base URL:** `http://localhost:5000` **Authentication:** All requests must include the header: `Authorization: Bearer <YOUR_API_KEY>`

### 1. Artifacts (Files)

`POST /artifacts` (Upload)

Supports Single File upload or Batch upload with a metadata map.
- **Single File Example:**

  ```
  curl -X POST http://localhost:5000/artifacts \
    -H "Authorization: Bearer change-me-locally" \
    -F "file=@myimage.png" \
    -F "title=My Single Image" \
    -F "drone_id=Drone-Alpha-007"
  ```
- **Batch Upload Example:** Requires a `my_metadata.json` map file.

  ```
  curl -X POST http://localhost:5000/artifacts \
    -H "Authorization: Bearer change-me-locally" \
    -F "file=@img1.png" \
    -F "file=@img2.png" \
    -F "metadata_map=$(< my_metadata.json)"
  ```

`GET /artifacts` (Query)

Get a list of artifacts. Supports filtering and field selection.
- **List All:**

  ```
  curl -H "Authorization: Bearer change-me-locally" "http://localhost:5000/artifacts"
  ```
- **Filter & Select Fields:**

  ```
  curl -H "Authorization: Bearer change-me-locally" \
    "http://localhost:5000/artifacts?drone_id=Drone-Alpha-007&fields=filename,location"
  ```

`GET /artifacts/<artifact_id>` (Get One)

Get details for a specific artifact.

```
curl -H "Authorization: Bearer change-me-locally" \
  "http://localhost:5000/artifacts/3b62f2b9-3038-42ef-90c4-a0b490641af7"
```

### 2. Sensor Readings (Data)

`POST /sensor-readings`

Upload a single structured sensor reading.

```
curl -X POST http://localhost:5000/sensor-readings \
  -H "Authorization: Bearer change-me-locally" \
  -H "Content-Type: application/json" \
  -d '{
    "sensor_id": "Sensor-A1",
    "temperature": 24.5,
    "humidity": 60.2,
    "uv_intensity": 4.5,
    "timestamp": "2025-11-20T10:00:00Z"
  }'
```

## Accessing Internal Components

### Directus Archive Portal

- **URL:** [http://localhost:8055](http://localhost:8055)
- **Initial Setup:** First-time users will create an admin account on first access
- **Backend:** Content management system (Directus) for database administration
- **Frontend:** Custom HTML/CSS/JavaScript portal with server-side rendering

#### Portal Features

**Public Pages (No Authentication Required):**
- **Home (`/`)**: Landing page with archive statistics and featured collections
- **Toolbox (`/archive/toolbox`)**: Directory of TEXTaiLES tools (AmalthAI, THOTH, ZEUSbot, NEPHELE, HESTIA, INDRA) with documentation and repo links

**Authenticated Pages (Login Required):**
- **Collections (`/archive/collections`)**: 
  - Browse artifacts by use case 
  - Card-based grid view with thumbnails
- **Artifact Details (`/archive/artefacts/:id`)**: 
  - Full artifact metadata (heritage asset info, digital asset details)
  - 3D model viewer integration (ATON framework for GLB files)
- **Add Artifact (`/archive/artefact/new`)**: 
  - Form-based artifact creation with file uploads
  - Support for GLB, images, and documents
  - Automatic metadata extraction

#### Technical Architecture

- **Rendering Engine**: Directus custom endpoints with Node.js + Express
- **Templating**: Server-side HTML generation with JavaScript template functions
- **Authentication**: Cookie-based sessions integrated with Directus auth
- **3D Viewer**: ATON framework for interactive GLB visualization
- **Python Integration**: Headless 3D rendering (pyrender + trimesh) for thumbnail generation

### MinIO (Object Storage)

- **URL:** [http://localhost:9001](https://www.google.com/search?q=http://localhost:9001 "null")
- **User:** `minioadmin` (or value in `.env`)
- **Pass:** `minioadmin` (or value in `.env`)

### Postgres (Database)

To inspect the database tables directly:

```
sudo docker exec -it postgres psql -U admin -d mydb -c "\dt"
```

Query the new sensor table:

```
sudo docker exec -it postgres psql -U admin -d mydb -c "SELECT * FROM sensor_readings LIMIT 5;"
```

### Swagger UI (Auto-Documentation)

- **URL:** [http://localhost:5000/docs](https://www.google.com/search?q=http://localhost:5000/docs "null")