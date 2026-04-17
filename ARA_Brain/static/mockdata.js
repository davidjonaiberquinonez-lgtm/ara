// ara brain/mockdata.js
const BD_Prueba = {
    usuarios: [
        { id: "3", nombre: "David", rol: "admin", password: "123" },
        { id: "22", nombre: "Juan", rol: "preparador", password: "123" },
        { id: "83", nombre: "Carlos", rol: "supervisor", password: "123" }
    ],
    inventario: [
        { 
            codigo: "7210783", 
            descripcion: "Ibuprofeno 800mg", 
            ubicacion: "MDI01-P4" 
        },
        { 
            codigo: "7210784", 
            descripcion: "Acetaminofen 650mg", 
            ubicacion: "MDA06-P4" 
        }
    ]
};

console.log("Sistema Ara: Base de datos de prueba cargada correctamente.");