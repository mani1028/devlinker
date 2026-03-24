from flask import Flask, jsonify

app = Flask(__name__)


@app.route("/api/test")
def test() -> tuple[dict[str, str], int]:
    return jsonify({"message": "Backend working!"}), 200


if __name__ == "__main__":
    app.run(port=5000)
