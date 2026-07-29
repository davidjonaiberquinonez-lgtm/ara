from flask import Flask, render_template, send_from_directory
import os

app = Flask(__name__)

@app.route('/')
def index():
    # Flask busca automáticamente en la carpeta 'templates'
    return render_template('index.html')

# Esta ruta es para que el HTML pueda encontrar el mockdata.js en 'static'
@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)

if __name__ == '__main__':
    app.run(host='192.168.6.105', port=5000, debug=True)