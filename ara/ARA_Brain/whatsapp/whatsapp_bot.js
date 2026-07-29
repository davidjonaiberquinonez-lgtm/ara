const { Client, LocalAuth } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

// IP de tu servidor Flask
const SERVER_URL = "http://192.168.1.44:5000"; 

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

// Detectar tipo de mensaje entrante
function detectarTipo(msg) {
    if (msg.hasMedia) {
        if (msg.type === 'image') return 'image';
        if (msg.type === 'audio' || msg.type === 'ptt') return 'audio';
        return 'file';
    }
    return 'texto';
}

// Escuchar mensajes entrantes
client.on('message', async msg => {
    try {
        // Normaliza el teléfono del remitente (sin sufijo @c.us)
        const telefonoLimpio = msg.from.replace(/@c\.us$/, '');

        // Si trae multimedia, la descargamos como base64 para almacenarla
        let contenido = msg.body;
        let tipo = detectarTipo(msg);
        if (msg.hasMedia && (tipo === 'image' || tipo === 'audio' || tipo === 'file')) {
            try {
                const media = await msg.downloadMedia();
                if (media) {
                    contenido = `data:${media.mimetype};base64,${media.data}`;
                }
            } catch (e) {
                console.warn("No se pudo descargar media:", e.message);
            }
        }

        // Enviamos el mensaje al webhook del servidor Flask (Ara Server)
        const response = await axios.post(`${SERVER_URL}/api/chat/webhook`, {
            usuario: telefonoLimpio,   // número de teléfono del cliente
            nombre:  msg._data?.notifyName || telefonoLimpio,
            mensaje: contenido,
            tipo:    tipo
        });

        // El servidor NO responde automáticamente: el agente responde desde la UI.
        // Aquí sólo confirmamos recepción en consola.
        if (response.data && response.data.status === 'success') {
            console.log(`📩 Mensaje de ${telefonoLimpio} almacenado (conv ${response.data.conversacion_id || 'n/a'})`);
        }
    } catch (error) {
        console.error("Error procesando mensaje:", error.response?.status, error.message);
    }
});

client.initialize();
