from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import psycopg2
import psycopg2.extras
from datetime import datetime, date
import os
import uuid

# ============================================
# CONFIGURACIÓN
# ============================================

DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    raise Exception("DATABASE_URL no configurada. Agrega la variable de entorno.")

app = FastAPI(
    title="API Inventario Teléfonos",
    description="Sistema de inventario para tienda de teléfonos",
    version="1.0.0"
)

# CORS - Permite conexiones desde cualquier origen
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================
# MODELOS DE DATOS (Pydantic)
# ============================================

class UsuarioCreate(BaseModel):
    email: str
    password: str
    nombre: str
    rol: str = "vendedor"  # admin o vendedor

class UsuarioResponse(BaseModel):
    id: str
    email: str
    nombre: str
    rol: str
    activo: bool

class EquipoCreate(BaseModel):
    modelo_id: int
    imei: str
    color: str
    almacenamiento: str
    precio_compra: float
    precio_venta: float
    observaciones: Optional[str] = None

class EquipoResponse(BaseModel):
    id: int
    modelo_id: int
    imei: str
    color: str
    almacenamiento: str
    estado: str
    precio_venta: float
    marca: str
    modelo: str

class VentaCreate(BaseModel):
    equipo_id: int
    precio_final: float
    metodo_pago: str
    cliente_nombre: Optional[str] = None
    cliente_telefono: Optional[str] = None
    observaciones: Optional[str] = None

class StockResponse(BaseModel):
    marca: str
    modelo: str
    disponibles: int
    precio_desde: Optional[float] = None

class LoginRequest(BaseModel):
    email: str
    password: str

# ============================================
# FUNCIONES DE BASE DE DATOS
# ============================================

def get_db():
    """Obtener conexión a la base de datos"""
    return psycopg2.connect(DATABASE_URL)

def verificar_rol(usuario_id: str, rol_requerido: str):
    """Verificar si un usuario tiene el rol requerido"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute(
            "SELECT rol FROM usuarios WHERE id = %s AND activo = TRUE",
            (usuario_id,)
        )
        usuario = cursor.fetchone()
        if not usuario:
            return False
        if rol_requerido == "admin" and usuario["rol"] != "admin":
            return False
        return True
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - PÚBLICOS
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

# ============================================
# ENDPOINTS - STOCK Y EQUIPOS
# ============================================

@app.get("/api/stock", response_model=List[StockResponse])
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
    """Listar equipos (opcionalmente por estado)"""
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
    """Buscar equipos por IMEI (búsqueda parcial)"""
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

@app.get("/api/equipos/stock-modelo/{modelo_id}")
def get_stock_modelo(modelo_id: int):
    """Obtener cuántos equipos hay de un modelo específico"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                m.nombre AS modelo,
                ma.nombre AS marca,
                COUNT(e.id) AS disponibles,
                MIN(e.precio_venta) AS precio_desde
            FROM equipos e
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
            WHERE e.modelo_id = %s AND e.estado = 'disponible'
            GROUP BY m.nombre, ma.nombre
        """, (modelo_id,))
        return cursor.fetchone() or {"disponibles": 0}
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - REGISTRO DE EQUIPOS (SOLO ADMIN)
# ============================================

@app.post("/api/equipos", response_model=dict)
def registrar_equipo(equipo: EquipoCreate, usuario_id: str):
    """
    Registrar un nuevo equipo (solo administradores)
    """
    # Verificar que el usuario es admin
    if not verificar_rol(usuario_id, "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores pueden registrar equipos"
        )
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Verificar que el modelo existe
        cursor.execute("SELECT id FROM modelos WHERE id = %s", (equipo.modelo_id,))
        if not cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Modelo no encontrado"
            )
        
        # Verificar que el IMEI no esté duplicado
        cursor.execute("SELECT id FROM equipos WHERE imei = %s", (equipo.imei,))
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El IMEI ya está registrado"
            )
        
        # Insertar el equipo
        cursor.execute("""
            INSERT INTO equipos (
                modelo_id, imei, color, almacenamiento,
                precio_compra, precio_venta, observaciones,
                registrado_por
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            equipo.modelo_id,
            equipo.imei,
            equipo.color,
            equipo.almacenamiento,
            equipo.precio_compra,
            equipo.precio_venta,
            equipo.observaciones,
            uuid.UUID(usuario_id)
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en base de datos: {str(e)}"
        )
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - VENTAS (VENDEDOR Y ADMIN)
# ============================================

@app.post("/api/ventas", response_model=dict)
def registrar_venta(venta: VentaCreate, usuario_id: str):
    """
    Registrar una venta (vendedores y administradores)
    """
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Verificar que el equipo existe y está disponible
        cursor.execute("""
            SELECT id, estado, precio_venta 
            FROM equipos 
            WHERE id = %s
        """, (venta.equipo_id,))
        
        equipo = cursor.fetchone()
        if not equipo:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Equipo no encontrado"
            )
        
        if equipo["estado"] != "disponible":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"El equipo no está disponible (estado: {equipo['estado']})"
            )
        
        # Registrar la venta
        cursor.execute("""
            INSERT INTO ventas (
                equipo_id, vendedor_id, precio_final,
                metodo_pago, cliente_nombre, cliente_telefono,
                observaciones
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            venta.equipo_id,
            uuid.UUID(usuario_id),
            venta.precio_final,
            venta.metodo_pago,
            venta.cliente_nombre,
            venta.cliente_telefono,
            venta.observaciones
        ))
        
        venta_id = cursor.fetchone()["id"]
        
        # Actualizar estado del equipo
        cursor.execute("""
            UPDATE equipos 
            SET estado = 'vendido', 
                vendido_por = %s,
                fecha_venta = NOW()
            WHERE id = %s
        """, (uuid.UUID(usuario_id), venta.equipo_id))
        
        conn.commit()
        
        return {
            "success": True,
            "venta_id": venta_id,
            "message": "Venta registrada correctamente"
        }
    
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en base de datos: {str(e)}"
        )
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - REPORTES
# ============================================

@app.get("/api/reportes/ventas-hoy")
def get_ventas_hoy():
    """Resumen de ventas del día"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM vw_ventas_hoy")
        return cursor.fetchone() or {"total_ventas": 0, "total_ingresos": 0, "vendedores_activos": 0}
    finally:
        cursor.close()
        conn.close()

@app.get("/api/reportes/resumen")
def get_resumen():
    """Resumen general del inventario"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT * FROM vw_resumen_general")
        return cursor.fetchone() or {}
    finally:
        cursor.close()
        conn.close()

@app.get("/api/reportes/ventas")
def get_ventas(fecha_inicio: Optional[str] = None, fecha_fin: Optional[str] = None):
    """Listar ventas con filtros de fecha"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        query = """
            SELECT 
                v.id,
                v.fecha_venta,
                e.imei,
                ma.nombre AS marca,
                m.nombre AS modelo,
                v.precio_final,
                v.metodo_pago,
                v.cliente_nombre,
                u.nombre AS vendedor
            FROM ventas v
            JOIN equipos e ON v.equipo_id = e.id
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
            LEFT JOIN usuarios u ON v.vendedor_id = u.id
            WHERE 1=1
        """
        params = []
        
        if fecha_inicio:
            query += " AND v.fecha_venta >= %s"
            params.append(fecha_inicio)
        if fecha_fin:
            query += " AND v.fecha_venta <= %s"
            params.append(fecha_fin)
        
        query += " ORDER BY v.fecha_venta DESC"
        
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - USUARIOS (SOLO ADMIN)
# ============================================

@app.get("/api/usuarios", response_model=List[UsuarioResponse])
def get_usuarios(usuario_id: str):
    """Listar todos los usuarios (solo administradores)"""
    if not verificar_rol(usuario_id, "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores pueden ver usuarios"
        )
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT id, email, nombre, rol, activo
            FROM usuarios
            ORDER BY created_at DESC
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.post("/api/usuarios", response_model=dict)
def crear_usuario(usuario: UsuarioCreate, usuario_id: str):
    """Crear un nuevo usuario (solo administradores)"""
    if not verificar_rol(usuario_id, "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores pueden crear usuarios"
        )
    
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Verificar que el email no exista
        cursor.execute("SELECT id FROM usuarios WHERE email = %s", (usuario.email,))
        if cursor.fetchone():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El email ya está registrado"
            )
        
        # NOTA: La contraseña se maneja en la autenticación de Supabase
        # Aquí solo creamos el perfil en la tabla usuarios
        cursor.execute("""
            INSERT INTO usuarios (email, nombre, rol)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (usuario.email, usuario.nombre, usuario.rol))
        
        new_id = cursor.fetchone()["id"]
        conn.commit()
        
        return {
            "success": True,
            "id": new_id,
            "message": "Usuario creado correctamente"
        }
    
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en base de datos: {str(e)}"
        )
    finally:
        cursor.close()
        conn.close()

@app.put("/api/usuarios/{id_usuario}")
def actualizar_usuario(id_usuario: str, usuario: UsuarioCreate, usuario_id: str):
    """Actualizar un usuario (solo administradores)"""
    if not verificar_rol(usuario_id, "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores pueden modificar usuarios"
        )
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE usuarios 
            SET nombre = %s, rol = %s, activo = %s
            WHERE id = %s
        """, (usuario.nombre, usuario.rol, usuario.activo, id_usuario))
        
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuario no encontrado"
            )
        
        conn.commit()
        return {"success": True, "message": "Usuario actualizado correctamente"}
    
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en base de datos: {str(e)}"
        )
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/usuarios/{id_usuario}")
def eliminar_usuario(id_usuario: str, usuario_id: str):
    """Eliminar (desactivar) un usuario (solo administradores)"""
    if not verificar_rol(usuario_id, "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo administradores pueden eliminar usuarios"
        )
    
    # No permitir eliminar a sí mismo
    if id_usuario == usuario_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes eliminarte a ti mismo"
        )
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE usuarios SET activo = FALSE WHERE id = %s
        """, (id_usuario,))
        
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuario no encontrado"
            )
        
        conn.commit()
        return {"success": True, "message": "Usuario desactivado correctamente"}
    
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error en base de datos: {str(e)}"
        )
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - SUGERENCIAS (AUTOCOMPLETADO)
# ============================================

@app.get("/api/sugerencias/imei/{imei_parcial}")
def get_sugerencias_imei(imei_parcial: str):
    """Obtener sugerencias de equipos por IMEI para autocompletado"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                e.imei,
                ma.nombre AS marca,
                m.nombre AS modelo,
                e.color,
                e.almacenamiento,
                e.precio_venta
            FROM equipos e
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
            WHERE e.imei ILIKE %s
            AND e.estado = 'disponible'
            LIMIT 10
        """, (f'%{imei_parcial}%',))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.get("/api/modelos")
def get_modelos():
    """Listar todos los modelos para el selector"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                m.id,
                ma.nombre AS marca,
                m.nombre AS modelo
            FROM modelos m
            JOIN marcas ma ON m.marca_id = ma.id
            ORDER BY ma.nombre, m.nombre
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

# ============================================
# INICIO DEL SERVIDOR
# ============================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)