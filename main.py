from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import psycopg2
import psycopg2.extras
from datetime import datetime, date
import os
import uuid

# ============================================
# CONFIGURACIÓN - HARCODEADO
# ============================================

# URL de conexión DIRECTA a Supabase
DATABASE_URL = "postgresql://postgres:11CNSQJUQ0s1vuGUDELtqG@db.tziufvisbvljkvhnbneu.supabase.co:5432/postgres"

print(f"✅ Conectando a: {DATABASE_URL[:40]}...")

app = FastAPI(
    title="API Inventario Teléfonos",
    description="Sistema de inventario para tienda de teléfonos",
    version="1.0.0"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# MODELOS DE DATOS
# ============================================

class EquipoCreate(BaseModel):
    modelo_id: int
    imei: str
    color: str
    almacenamiento: str
    precio_compra: float
    precio_venta: float
    observaciones: Optional[str] = None

class VentaCreate(BaseModel):
    equipo_id: int
    precio_final: float
    metodo_pago: str
    cliente_nombre: Optional[str] = None
    cliente_telefono: Optional[str] = None
    observaciones: Optional[str] = None

# ============================================
# FUNCIONES DE BASE DE DATOS
# ============================================

def get_db():
    """Obtener conexión a la base de datos"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"❌ Error conectando a la base de datos: {e}")
        raise

# ============================================
# ENDPOINTS
# ============================================

@app.get("/")
def root():
    return {
        "message": "API Inventario Teléfonos",
        "version": "1.0.0",
        "status": "online"
    }

@app.get("/api/health")
def health_check():
    """Verificar que la API está funcionando"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": "error", "error": str(e)}

@app.get("/api/stock")
def get_stock():
    """Obtener stock disponible por modelo"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                ma.nombre AS marca,
                m.nombre AS modelo,
                COUNT(e.id) AS disponibles,
                MIN(e.precio_venta) AS precio_desde
            FROM equipos e
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
            WHERE e.estado = 'disponible'
            GROUP BY ma.nombre, m.nombre
            ORDER BY ma.nombre, m.nombre
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.get("/api/equipos")
def get_equipos(estado: Optional[str] = None):
    """Listar equipos"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        query = """
            SELECT 
                e.id,
                e.imei,
                ma.nombre AS marca,
                m.nombre AS modelo,
                e.color,
                e.almacenamiento,
                e.estado,
                e.precio_venta
            FROM equipos e
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
        """
        params = []
        if estado:
            query += " WHERE e.estado = %s"
            params.append(estado)
        query += " ORDER BY e.id DESC"
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.get("/api/equipos/imei/{imei}")
def buscar_por_imei(imei: str):
    """Buscar equipos por IMEI"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                e.id,
                e.imei,
                ma.nombre AS marca,
                m.nombre AS modelo,
                e.color,
                e.almacenamiento,
                e.estado,
                e.precio_venta
            FROM equipos e
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
            WHERE e.imei ILIKE %s
            AND e.estado = 'disponible'
            LIMIT 10
        """, (f'%{imei}%',))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.post("/api/equipos")
def registrar_equipo(equipo: EquipoCreate):
    """Registrar un nuevo equipo"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT id FROM modelos WHERE id = %s", (equipo.modelo_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Modelo no encontrado")
        
        cursor.execute("SELECT id FROM equipos WHERE imei = %s", (equipo.imei,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="El IMEI ya está registrado")
        
        cursor.execute("""
            INSERT INTO equipos (
                modelo_id, imei, color, almacenamiento,
                precio_compra, precio_venta, observaciones
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            equipo.modelo_id,
            equipo.imei,
            equipo.color,
            equipo.almacenamiento,
            equipo.precio_compra,
            equipo.precio_venta,
            equipo.observaciones
        ))
        
        equipo_id = cursor.fetchone()["id"]
        conn.commit()
        
        return {
            "success": True,
            "id": equipo_id,
            "message": "Equipo registrado correctamente"
        }
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/ventas")
def registrar_venta(venta: VentaCreate):
    """Registrar una venta"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT id, estado FROM equipos WHERE id = %s", (venta.equipo_id,))
        equipo = cursor.fetchone()
        if not equipo:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")
        if equipo["estado"] != "disponible":
            raise HTTPException(status_code=400, detail="El equipo no está disponible")
        
        cursor.execute("""
            INSERT INTO ventas (equipo_id, precio_final, metodo_pago, cliente_nombre, cliente_telefono, observaciones)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            venta.equipo_id,
            venta.precio_final,
            venta.metodo_pago,
            venta.cliente_nombre,
            venta.cliente_telefono,
            venta.observaciones
        ))
        
        venta_id = cursor.fetchone()["id"]
        
        cursor.execute("UPDATE equipos SET estado = 'vendido', fecha_venta = NOW() WHERE id = %s", (venta.equipo_id,))
        
        conn.commit()
        
        return {
            "success": True,
            "venta_id": venta_id,
            "message": "Venta registrada correctamente"
        }
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/modelos")
def get_modelos():
    """Listar todos los modelos"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT m.id, ma.nombre AS marca, m.nombre AS modelo
            FROM modelos m
            JOIN marcas ma ON m.marca_id = ma.id
            ORDER BY ma.nombre, m.nombre
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.get("/api/reportes/ventas-hoy")
def get_ventas_hoy():
    """Resumen de ventas del día"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                COUNT(*) as total_ventas,
                COALESCE(SUM(precio_final), 0) as total_ingresos
            FROM ventas
            WHERE DATE(fecha_venta) = CURRENT_DATE
        """)
        return cursor.fetchone() or {"total_ventas": 0, "total_ingresos": 0}
    finally:
        cursor.close()
        conn.close()

# ============================================
# INICIO DEL SERVIDOR
# ============================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)