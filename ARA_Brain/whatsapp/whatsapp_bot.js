const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

// IP de tu servidor Flask
const SERVER_URL = "http://192.168.6.77:5000"; 

const client = new Client({
    authStrategy: new LocalAuth() // Guarda la sesión para no escanear el QR cada vez
});

// Generar el código QR en la terminal
client.on('qr', (qr) => {
    qrcode.generate(qr, {small: true});
    console.log('📱 ESCANEA EL QR con tu WhatsApp para iniciar el Bot');
});

client.on('ready', () => {
    console.log('✅ ARA WhatsApp Bot conectado y listo!');
});

// Escuchar mensajes entrantes
client.on('message', async msg => {
    try {
        // Enviamos el mensaje al servidor de Python (ARA Server)
        const response = await axios.post(`${SERVER_URL}/api/whatsapp`, {
            usuario: msg.from, // Número de teléfono del cliente
            mensaje: msg.body  // El texto que escribió
        });

        if (response.data.status === "success") {
            msg.reply(response.data.respuesta);
        }
    } catch (error) {
        console.error("Error procesando mensaje:", error.message);
    }
});

client.initialize();
