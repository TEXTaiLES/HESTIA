# Wraps the dynamo:v1 simulator in a Flask service.
#
# The base image already has python3.9, numpy, pint, jsonschema, and all the
# DynaMo modules baked into /working_environment/ScriptCollection. We layer
# Flask + MinIO + gunicorn on top and override the entrypoint so the container
# stays alive serving HTTP instead of running TEXTaiLES_DynaMo_Tool.py once
# and exiting.
FROM dynamo:v1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Apply image patches before launching. See patches/patch_texmath.py for the
# rationale (scipy cubic-spline fallback needed for low-node inputs).
COPY patches/ /app/patches/
RUN python3 /app/patches/patch_texmath.py

COPY app.py .

EXPOSE 5001

# DynaMo writes to CWD and shells out to FISFT.FFM with relative paths. The
# handler creates a per-request tempdir and runs the simulator as a subprocess
# with cwd=tempdir, so worker concurrency is safe.
ENTRYPOINT ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "2", "--timeout", "600", "--access-logfile", "-", "app:app"]
