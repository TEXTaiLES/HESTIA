docker compose up --build -d

for file in connectors/*.json; do
        echo -e "\nConnecting $file..."
        curl -X POST "http://localhost:8083/connectors" -H "Content-Type: application/json" --data "@$file"
done
