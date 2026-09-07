from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
import os
import uuid
import bcrypt
import jwt

# ============================================
# CONFIGURACIÓN
# ============================================

DATABASE_URL = "postgresql://postgres.tziufvisbvljkvhnbneu:11CNSQJUQ0s1vuGUDELtqG@aws-0-us-east-2.pooler.supabase.com:5432/postgres"

# Clave secreta para JWT - ¡CAMBIAR EN PRODUCCIÓN!
SECRET_KEY = "tu_clave_secreta_muy_segura_cambiar_en_produccion_2026"
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

print(f"✅ Conectando a: {DATABASE_URL[:40]}...")

app = FastAPI(
    title="API Inventario Teléfonos",
    description="Sistema de inventario para tienda de teléfonos",
    version="2.0.0"
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
# FUNCIONES DE SEGURIDAD
# ============================================

def hash_password(password: str) -> str:
    """Hashear una contraseña con bcrypt"""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    """Verificar una contraseña contra su hash"""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

def create_jwt_token(user_id: str, email: str, rol: str) -> str:
    """Crear un token JWT"""
    payload = {
        "sub": user_id,
        "email": email,
        "rol": rol,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_jwt_token(token: str) -> dict:
    """Verificar un token JWT"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado. Vuelve a iniciar sesión.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido.")

# ============================================
# DEPENDENCIAS PARA PROTEGER ENDPOINTS
# ============================================

def get_current_user(authorization: str = Header(None)):
    """Obtener el usuario actual desde el token JWT"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Token requerido. Inicia sesión primero.")
    
    try:
        scheme, token = authorization.split(" ")
        if scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="Formato de token inválido. Usa 'Bearer [token]'")
        
        payload = verify_jwt_token(token)
        return payload
    except ValueError:
        raise HTTPException(status_code=401, detail="Formato de token inválido.")

def get_current_admin(user = Depends(get_current_user)):
    """Verificar que el usuario sea admin"""
    if user.get("rol") != "admin":
        raise HTTPException(status_code=403, detail="Se requieren permisos de administrador.")
    return user

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

class UsuarioCreate(BaseModel):
    email: str
    password: str
    nombre: str
    rol: str = "vendedor"

class LoginRequest(BaseModel):
    email: str
    password: str
    device: str = "web"

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
# ENDPOINTS - AUTENTICACIÓN
# ============================================

@app.post("/api/auth/login")
def login(request: LoginRequest):
    """
    Autenticar usuario según el dispositivo
    - web: SOLO admins (rol = 'admin')
    - mobile: admins y vendedores
    """
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Buscar usuario por email
        cursor.execute("""
            SELECT id, email, nombre, rol, activo, password_hash
            FROM usuarios
            WHERE email = %s AND activo = TRUE
        """, (request.email,))
        
        usuario = cursor.fetchone()
        
        if not usuario:
            raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")
        
        # Verificar contraseña con bcrypt
        if not verify_password(request.password, usuario["password_hash"]):
            raise HTTPException(status_code=401, detail="Contraseña incorrecta")
        
        # REGLA: SOLO ADMINISTRADORES EN PANEL WEB
        if request.device == "web" and usuario["rol"] != "admin":
            raise HTTPException(
                status_code=403, 
                detail="Acceso denegado. Solo administradores pueden acceder al panel web."
            )
        
        # Crear JWT token
        token = create_jwt_token(
            str(usuario["id"]),
            usuario["email"],
            usuario["rol"]
        )
        
        return {
            "success": True,
            "id": usuario["id"],
            "email": usuario["email"],
            "nombre": usuario["nombre"],
            "rol": usuario["rol"],
            "token": token
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/auth/migrar-passwords")
def migrar_passwords(admin = Depends(get_current_admin)):
    """Migrar contraseñas en texto plano a hashed (solo admin)"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT id, email, password_hash 
            FROM usuarios 
            WHERE password_hash IS NOT NULL 
            AND password_hash != ''
            AND (LENGTH(password_hash) < 50 OR password_hash NOT LIKE '$2b$%')
        """)
        
        usuarios = cursor.fetchall()
        actualizados = 0
        
        for u in usuarios:
            hashed = hash_password(u["password_hash"])
            cursor.execute(
                "UPDATE usuarios SET password_hash = %s WHERE id = %s",
                (hashed, u["id"])
            )
            actualizados += 1
        
        conn.commit()
        return {
            "success": True,
            "message": f"Se actualizaron {actualizados} contraseñas"
        }
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
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
        "version": "2.0.0",
        "status": "online",
        "secure": True
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
# ENDPOINTS - STOCK Y EQUIPOS (PÚBLICOS)
# ============================================

@app.get("/api/stock")
def get_stock():
    """Obtener stock disponible por modelo (público)"""
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

@app.get("/api/modelos")
def get_modelos():
    """Listar todos los modelos (público)"""
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

# ============================================
# ENDPOINTS - EQUIPOS (PROTEGIDOS)
# ============================================

@app.get("/api/equipos")
def get_equipos(estado: Optional[str] = None, user = Depends(get_current_user)):
    """Listar equipos (requiere autenticación)"""
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
def buscar_por_imei(imei: str, user = Depends(get_current_user)):
    """Buscar equipos por IMEI (requiere autenticación)"""
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
def registrar_equipo(equipo: EquipoCreate, admin = Depends(get_current_admin)):
    """Registrar un nuevo equipo (solo admin)"""
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

# ============================================
# ENDPOINTS - VENTAS (PROTEGIDOS)
# ============================================

@app.post("/api/ventas")
def registrar_venta(venta: VentaCreate, user = Depends(get_current_user)):
    """Registrar una venta (admin o vendedor)"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT id, estado FROM equipos WHERE id = %s", (venta.equipo_id,))
        equipo = cursor.fetchone()
        if not equipo:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")
        if equipo["estado"] != "disponible":
            raise HTTPException(status_code=400, detail="El equipo no está disponible")
        
        # Registrar venta con el usuario actual
        cursor.execute("""
            INSERT INTO ventas (equipo_id, vendedor_id, precio_final, metodo_pago, cliente_nombre, cliente_telefono, observaciones)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            venta.equipo_id,
            uuid.UUID(user["sub"]),
            venta.precio_final,
            venta.metodo_pago,
            venta.cliente_nombre,
            venta.cliente_telefono,
            venta.observaciones
        ))
        
        venta_id = cursor.fetchone()["id"]
        
        cursor.execute("UPDATE equipos SET estado = 'vendido', fecha_venta = NOW(), vendido_por = %s WHERE id = %s", 
                       (uuid.UUID(user["sub"]), venta.equipo_id))
        
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

# ============================================
# ENDPOINTS - REPORTES (PROTEGIDOS)
# ============================================

@app.get("/api/reportes/ventas-hoy")
def get_ventas_hoy(user = Depends(get_current_user)):
    """Resumen de ventas del día (requiere autenticación)"""
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

@app.get("/api/reportes/ventas")
def get_ventas(fecha_inicio: Optional[str] = None, fecha_fin: Optional[str] = None, user = Depends(get_current_user)):
    """Listar ventas con filtros de fecha (requiere autenticación)"""
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

@app.get("/api/reportes/resumen")
def get_resumen(user = Depends(get_current_user)):
    """Resumen general del inventario (requiere autenticación)"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                (SELECT COUNT(*) FROM equipos) AS total_equipos,
                (SELECT COUNT(*) FROM equipos WHERE estado = 'disponible') AS disponibles,
                (SELECT COUNT(*) FROM equipos WHERE estado = 'vendido') AS vendidos,
                (SELECT COUNT(*) FROM usuarios WHERE activo = TRUE) AS usuarios_activos
        """)
        return cursor.fetchone() or {}
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - USUARIOS (SOLO ADMIN)
# ============================================

@app.get("/api/usuarios")
def get_usuarios(admin = Depends(get_current_admin)):
    """Listar todos los usuarios (solo admin)"""
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

@app.post("/api/usuarios")
def crear_usuario(usuario: UsuarioCreate, admin = Depends(get_current_admin)):
    """Crear un nuevo usuario (solo admin)"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Verificar que el email no exista
        cursor.execute("SELECT id FROM usuarios WHERE email = %s", (usuario.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="El email ya está registrado")
        
        # Hashear contraseña
        hashed_password = hash_password(usuario.password)
        
        cursor.execute("""
            INSERT INTO usuarios (email, nombre, rol, password_hash)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, (usuario.email, usuario.nombre, usuario.rol, hashed_password))
        
        new_id = cursor.fetchone()["id"]
        conn.commit()
        
        return {
            "success": True,
            "id": new_id,
            "message": "Usuario creado correctamente"
        }
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/usuarios/{id_usuario}")
def actualizar_usuario(id_usuario: str, usuario: UsuarioCreate, admin = Depends(get_current_admin)):
    """Actualizar un usuario (solo admin)"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Verificar que el usuario existe
        cursor.execute("SELECT id FROM usuarios WHERE id = %s", (id_usuario,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
        # Hashear nueva contraseña si se proporciona
        hashed_password = hash_password(usuario.password) if usuario.password else None
        
        if hashed_password:
            cursor.execute("""
                UPDATE usuarios 
                SET email = %s, nombre = %s, rol = %s, password_hash = %s
                WHERE id = %s
            """, (usuario.email, usuario.nombre, usuario.rol, hashed_password, id_usuario))
        else:
            cursor.execute("""
                UPDATE usuarios 
                SET email = %s, nombre = %s, rol = %s
                WHERE id = %s
            """, (usuario.email, usuario.nombre, usuario.rol, id_usuario))
        
        conn.commit()
        return {"success": True, "message": "Usuario actualizado correctamente"}
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.delete("/api/usuarios/{id_usuario}")
def eliminar_usuario(id_usuario: str, admin = Depends(get_current_admin)):
    """Eliminar (desactivar) un usuario (solo admin)"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE usuarios SET activo = FALSE WHERE id = %s
        """, (id_usuario,))
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
        conn.commit()
        return {"success": True, "message": "Usuario desactivado correctamente"}
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()
@app.put("/api/usuarios/{id_usuario}/activar")
def activar_usuario(id_usuario: str, admin = Depends(get_current_admin)):
    """Activar un usuario (solo admin)"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE usuarios SET activo = TRUE WHERE id = %s
        """, (id_usuario,))
        
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
        conn.commit()
        return {"success": True, "message": "Usuario activado correctamente"}
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============================================
# INICIO DEL SERVIDOR
# ============================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)