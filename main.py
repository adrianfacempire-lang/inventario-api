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

SECRET_KEY = "tu_clave_secreta_muy_segura_cambiar_en_produccion_2026"
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

print(f"✅ Conectando a: {DATABASE_URL[:40]}...")

app = FastAPI(
    title="API Inventario Teléfonos",
    description="Sistema de inventario para tienda de teléfonos",
    version="2.0.0"
)

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
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

def create_jwt_token(user_id: str, email: str, rol: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "rol": rol,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def verify_jwt_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado. Vuelve a iniciar sesión.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido.")

# ============================================
# DEPENDENCIAS
# ============================================

def get_current_user(authorization: str = Header(None)):
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
    if user.get("rol") != "admin":
        raise HTTPException(status_code=403, detail="Se requieren permisos de administrador.")
    return user

# ============================================
# MODELOS DE DATOS
# ============================================

class EquipoCreate(BaseModel):
    modelo_id: int
    imei: str
    imei2: Optional[str] = None
    color: str
    almacenamiento: str
    precio_compra: float
    precio_venta: float
    observaciones: Optional[str] = None

class EquipoUpdate(BaseModel):
    modelo_id: int
    imei: str
    imei2: Optional[str] = None
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
    password: str = ""
    nombre: str
    rol: str = "vendedor"
    activo: bool = True

class LoginRequest(BaseModel):
    email: str
    password: str
    device: str = "web"

class RetirarEquipoRequest(BaseModel):
    equipo_id: int
    razon: str

# ============================================
# FUNCIONES DE BASE DE DATOS
# ============================================

def get_db():
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
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT id, email, nombre, rol, activo, password_hash
            FROM usuarios
            WHERE email = %s AND activo = TRUE
        """, (request.email,))
        
        usuario = cursor.fetchone()
        
        if not usuario:
            raise HTTPException(status_code=401, detail="Usuario no encontrado o inactivo")
        
        if not verify_password(request.password, usuario["password_hash"]):
            raise HTTPException(status_code=401, detail="Contraseña incorrecta")
        
        if request.device == "web" and usuario["rol"] != "admin":
            raise HTTPException(
                status_code=403, 
                detail="Acceso denegado. Solo administradores pueden acceder al panel web."
            )
        
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
# ENDPOINTS - VERIFICAR IMEI
# ============================================

@app.get("/api/equipos/verificar/{imei}")
def verificar_imei(imei: str, user = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                e.id,
                e.imei,
                e.imei2,
                ma.nombre AS marca,
                m.nombre AS modelo,
                e.color,
                e.almacenamiento,
                e.estado,
                e.precio_venta,
                e.fecha_ingreso
            FROM equipos e
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
            WHERE e.imei = %s OR e.imei2 = %s
        """, (imei, imei))
        
        resultado = cursor.fetchone()
        
        if resultado:
            return {"existe": True, "equipo": resultado}
        else:
            return {"existe": False}
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - TAC / IMEI
# ============================================

@app.get("/api/tac/{tac}")
def get_tac_info(tac: str, user = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        if len(tac) != 8 or not tac.isdigit():
            raise HTTPException(status_code=400, detail="El TAC debe tener 8 dígitos")
        
        cursor.execute("""
            SELECT id, tac, brand, model, model_number
            FROM device_models
            WHERE tac = %s
        """, (tac,))
        
        resultado = cursor.fetchone()
        
        if not resultado:
            return {
                "success": False,
                "tac": tac,
                "message": "TAC no encontrado en la base de datos",
                "brand": None,
                "model": None,
                "model_number": None
            }
        
        return {
            "success": True,
            "tac": resultado["tac"],
            "brand": resultado["brand"],
            "model": resultado["model"],
            "model_number": resultado["model_number"]
        }
    finally:
        cursor.close()
        conn.close()

@app.post("/api/tac")
def crear_tac(data: dict, admin = Depends(get_current_admin)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        tac = data.get("tac", "").strip()
        brand = data.get("brand", "").strip()
        model = data.get("model", "").strip()
        model_number = data.get("model_number", "").strip()
        
        if len(tac) != 8 or not tac.isdigit():
            raise HTTPException(status_code=400, detail="El TAC debe tener 8 dígitos")
        
        if not brand or not model:
            raise HTTPException(status_code=400, detail="Marca y modelo son requeridos")
        
        cursor.execute("""
            INSERT INTO device_models (tac, brand, model, model_number)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (tac) DO UPDATE SET
                brand = EXCLUDED.brand,
                model = EXCLUDED.model,
                model_number = EXCLUDED.model_number
            RETURNING id, tac, brand, model, model_number
        """, (tac, brand, model, model_number))
        
        resultado = cursor.fetchone()
        conn.commit()
        
        return {"success": True, "message": "TAC registrado correctamente", "data": resultado}
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.get("/api/tac")
def get_all_tacs(user = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT id, tac, brand, model, model_number, created_at
            FROM device_models
            ORDER BY brand, model
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - PÚBLICOS
# ============================================

@app.get("/")
def root():
    return {"message": "API Inventario Teléfonos", "version": "2.0.0", "status": "online", "secure": True}

@app.get("/api/health")
def health_check():
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
# ENDPOINTS - EQUIPOS
# ============================================

@app.get("/api/equipos")
def get_equipos(
    estado: Optional[str] = None,
    marca: Optional[str] = None,
    modelo: Optional[str] = None,
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
    user = Depends(get_current_user)
):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        query = """
            SELECT 
                e.id,
                e.imei,
                e.imei2,
                ma.nombre AS marca,
                m.nombre AS modelo,
                m.id AS modelo_id,
                e.color,
                e.almacenamiento,
                e.estado,
                e.precio_compra,
                e.precio_venta,
                e.observaciones,
                e.fecha_ingreso
            FROM equipos e
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
            WHERE 1=1
        """
        params = []
        
        if marca is not None and marca != '':
            query += " AND ma.nombre = %s"
            params.append(marca)
        
        if estado is not None and estado != '':
            query += " AND e.estado = %s"
            params.append(estado)
        
        if modelo is not None and modelo != '':
            query += " AND m.nombre ILIKE %s"
            params.append(f'%{modelo}%')
        
        if fecha_inicio is not None and fecha_inicio != '':
            query += " AND e.fecha_ingreso >= %s"
            params.append(fecha_inicio)
        
        if fecha_fin is not None and fecha_fin != '':
            query += " AND e.fecha_ingreso <= %s"
            params.append(fecha_fin)
        
        query += " ORDER BY e.id DESC"
        
        cursor.execute(query, params)
        return cursor.fetchall()
    except Exception as e:
        print(f"❌ ERROR: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

@app.get("/api/equipos/imei/{imei}")
def buscar_por_imei(imei: str, user = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                e.id, e.imei, e.imei2,
                ma.nombre AS marca, m.nombre AS modelo,
                e.color, e.almacenamiento, e.estado, e.precio_venta
            FROM equipos e
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
            WHERE (e.imei ILIKE %s OR e.imei2 ILIKE %s)
            AND e.estado = 'disponible'
            LIMIT 10
        """, (f'%{imei}%', f'%{imei}%'))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.post("/api/equipos")
def registrar_equipo(equipo: EquipoCreate, admin = Depends(get_current_admin)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # 1. Verificar modelo
        cursor.execute("""
            SELECT m.id, m.nombre, ma.nombre AS marca, ma.id AS marca_id
            FROM modelos m
            JOIN marcas ma ON m.marca_id = ma.id
            WHERE m.id = %s
        """, (equipo.modelo_id,))
        
        modelo_info = cursor.fetchone()
        if not modelo_info:
            raise HTTPException(status_code=404, detail="Modelo no encontrado")
        
        # 2. Verificar IMEI duplicado
        cursor.execute("SELECT id FROM equipos WHERE imei = %s OR imei2 = %s", (equipo.imei, equipo.imei))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="El IMEI ya está registrado")
        
        # 3. Verificar IMEI2 si se proporciona
        imei2_valor = equipo.imei2.strip() if equipo.imei2 and equipo.imei2.strip() != '' else None
        if imei2_valor:
            cursor.execute("SELECT id FROM equipos WHERE imei = %s OR imei2 = %s", (imei2_valor, imei2_valor))
            if cursor.fetchone():
                raise HTTPException(status_code=400, detail="El IMEI 2 ya está registrado")
        
        # 4. Guardar TAC automáticamente
        tac = equipo.imei[:8] if len(equipo.imei) >= 8 else None
        tac_guardado = False
        
        if tac:
            cursor.execute("SELECT id FROM device_models WHERE tac = %s", (tac,))
            if not cursor.fetchone():
                print(f"📝 Guardando nuevo TAC: {tac} → {modelo_info['marca']} {modelo_info['nombre']}")
                cursor.execute("""
                    INSERT INTO device_models (tac, brand, model, model_number)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (tac) DO NOTHING
                """, (tac, modelo_info['marca'], modelo_info['nombre'], ''))
                tac_guardado = True
        
        # 5. Insertar equipo
        cursor.execute("""
            INSERT INTO equipos (
                modelo_id, imei, imei2, color, almacenamiento,
                precio_compra, precio_venta, observaciones
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            equipo.modelo_id,
            equipo.imei,
            imei2_valor,
            equipo.color,
            equipo.almacenamiento,
            equipo.precio_compra,
            equipo.precio_venta,
            equipo.observaciones
        ))
        
        equipo_id = cursor.fetchone()["id"]
        conn.commit()
        
        mensaje = "Equipo registrado correctamente"
        if tac_guardado:
            mensaje += f". Nuevo TAC {tac} guardado en el catálogo"
        
        return {
            "success": True,
            "id": equipo_id,
            "tac": tac,
            "tac_guardado": tac_guardado,
            "message": mensaje
        }
        
    except HTTPException:
        raise
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/equipos/{equipo_id}")
def actualizar_equipo(equipo_id: int, equipo: EquipoUpdate, admin = Depends(get_current_admin)):
    """Actualizar un equipo existente (solo admin)"""
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        # Verificar que el equipo existe
        cursor.execute("SELECT id, estado FROM equipos WHERE id = %s", (equipo_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Equipo no encontrado")
        
        # Verificar modelo
        cursor.execute("SELECT id FROM modelos WHERE id = %s", (equipo.modelo_id,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Modelo no encontrado")
        
        # Verificar IMEI duplicado (excluyendo el actual)
        cursor.execute("SELECT id FROM equipos WHERE (imei = %s OR imei2 = %s) AND id != %s", 
                       (equipo.imei, equipo.imei, equipo_id))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="El IMEI ya está registrado en otro equipo")
        
        # Verificar IMEI2
        imei2_valor = equipo.imei2.strip() if equipo.imei2 and equipo.imei2.strip() != '' else None
        if imei2_valor:
            cursor.execute("SELECT id FROM equipos WHERE (imei = %s OR imei2 = %s) AND id != %s", 
                           (imei2_valor, imei2_valor, equipo_id))
            if cursor.fetchone():
                raise HTTPException(status_code=400, detail="El IMEI 2 ya está registrado en otro equipo")
        
        # Actualizar
        cursor.execute("""
            UPDATE equipos 
            SET modelo_id = %s,
                imei = %s,
                imei2 = %s,
                color = %s,
                almacenamiento = %s,
                precio_compra = %s,
                precio_venta = %s,
                observaciones = %s
            WHERE id = %s
            RETURNING id
        """, (
            equipo.modelo_id,
            equipo.imei,
            imei2_valor,
            equipo.color,
            equipo.almacenamiento,
            equipo.precio_compra,
            equipo.precio_venta,
            equipo.observaciones,
            equipo_id
        ))
        
        conn.commit()
        
        return {"success": True, "message": "Equipo actualizado correctamente"}
        
    except HTTPException:
        raise
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - VENTAS
# ============================================

@app.post("/api/ventas")
def registrar_venta(venta: VentaCreate, user = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT id, estado, imei FROM equipos WHERE id = %s", (venta.equipo_id,))
        equipo = cursor.fetchone()
        
        if not equipo:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")
        
        if equipo["estado"] != "disponible":
            raise HTTPException(status_code=400, detail=f"El equipo no está disponible (estado: {equipo['estado']})")
        
        user_id_str = user.get("sub")
        
        cursor.execute("SELECT id FROM usuarios WHERE id = %s", (user_id_str,))
        if not cursor.fetchone():
            raise HTTPException(status_code=400, detail="Usuario no encontrado en el sistema")
        
        cursor.execute("""
            INSERT INTO ventas (
                equipo_id, vendedor_id, precio_final, metodo_pago,
                cliente_nombre, cliente_telefono, observaciones
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            venta.equipo_id, user_id_str, venta.precio_final,
            venta.metodo_pago, venta.cliente_nombre, venta.cliente_telefono,
            venta.observaciones
        ))
        
        venta_id = cursor.fetchone()["id"]
        
        cursor.execute("""
            UPDATE equipos 
            SET estado = 'vendido', fecha_venta = NOW(), vendido_por = %s 
            WHERE id = %s
        """, (user_id_str, venta.equipo_id))
        
        conn.commit()
        
        return {
            "success": True,
            "venta_id": venta_id,
            "message": f"Venta registrada correctamente. Equipo: {equipo['imei']}"
        }
        
    except HTTPException:
        raise
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error en la base de datos: {str(e)}")
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Error inesperado: {str(e)}")
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - REPORTES
# ============================================

@app.get("/api/reportes/ventas")
def get_ventas(
    fecha_inicio: Optional[str] = None, 
    fecha_fin: Optional[str] = None,
    marca: Optional[str] = None,
    modelo: Optional[str] = None,
    user = Depends(get_current_user)
):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        query = """
            SELECT 
                v.id, v.fecha_venta, e.imei,
                ma.nombre AS marca, m.nombre AS modelo,
                v.precio_final, v.metodo_pago, v.cliente_nombre,
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
            query += " AND DATE(v.fecha_venta) >= %s"
            params.append(fecha_inicio)
        
        if fecha_fin:
            query += " AND DATE(v.fecha_venta) <= %s"
            params.append(fecha_fin)
        
        if marca:
            query += " AND ma.nombre ILIKE %s"
            params.append(f'%{marca}%')
        
        if modelo:
            query += " AND m.nombre ILIKE %s"
            params.append(f'%{modelo}%')
        
        query += " ORDER BY v.fecha_venta DESC"
        
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.get("/api/reportes/ventas-hoy")
def get_ventas_hoy(user = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                COUNT(*) as total_ventas,
                COALESCE(SUM(precio_final), 0) as total_ingresos,
                COUNT(DISTINCT vendedor_id) as vendedores_activos
            FROM ventas
            WHERE DATE(fecha_venta) = CURRENT_DATE
        """)
        return cursor.fetchone() or {"total_ventas": 0, "total_ingresos": 0, "vendedores_activos": 0}
    finally:
        cursor.close()
        conn.close()

@app.get("/api/reportes/resumen")
def get_resumen(user = Depends(get_current_user)):
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

@app.get("/api/reportes/stock-bajo")
def get_stock_bajo(limite: int = 3, user = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT 
                ma.nombre AS marca, m.nombre AS modelo,
                COUNT(e.id) AS disponibles, MIN(e.precio_venta) AS precio_desde
            FROM equipos e
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
            WHERE e.estado = 'disponible'
            GROUP BY ma.nombre, m.nombre
            HAVING COUNT(e.id) <= %s
            ORDER BY COUNT(e.id) ASC
        """, (limite,))
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.get("/api/reportes/exportar/ventas")
def exportar_ventas(
    fecha_inicio: Optional[str] = None, 
    fecha_fin: Optional[str] = None,
    marca: Optional[str] = None,
    modelo: Optional[str] = None,
    user = Depends(get_current_user)
):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        query = """
            SELECT 
                v.fecha_venta AS fecha, e.imei,
                ma.nombre AS marca, m.nombre AS modelo,
                v.precio_final AS precio, v.metodo_pago AS metodo_pago,
                v.cliente_nombre AS cliente, u.nombre AS vendedor
            FROM ventas v
            JOIN equipos e ON v.equipo_id = e.id
            JOIN modelos m ON e.modelo_id = m.id
            JOIN marcas ma ON m.marca_id = ma.id
            LEFT JOIN usuarios u ON v.vendedor_id = u.id
            WHERE 1=1
        """
        params = []
        
        if fecha_inicio:
            query += " AND DATE(v.fecha_venta) >= %s"
            params.append(fecha_inicio)
        if fecha_fin:
            query += " AND DATE(v.fecha_venta) <= %s"
            params.append(fecha_fin)
        if marca:
            query += " AND ma.nombre ILIKE %s"
            params.append(f'%{marca}%')
        if modelo:
            query += " AND m.nombre ILIKE %s"
            params.append(f'%{modelo}%')
        
        query += " ORDER BY v.fecha_venta DESC"
        
        cursor.execute(query, params)
        datos = cursor.fetchall()
        
        for item in datos:
            if item.get("fecha"):
                item["fecha"] = item["fecha"].isoformat() if hasattr(item["fecha"], 'isoformat') else str(item["fecha"])
        
        return {"success": True, "total": len(datos), "data": datos}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - MARCAS Y MODELOS
# ============================================

@app.get("/api/marcas")
def get_marcas(user = Depends(get_current_user)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT id, nombre FROM marcas ORDER BY nombre")
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.post("/api/marcas")
def crear_marca(marca: dict, admin = Depends(get_current_admin)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("INSERT INTO marcas (nombre) VALUES (%s) RETURNING id", (marca["nombre"],))
        new_id = cursor.fetchone()["id"]
        conn.commit()
        return {"id": new_id, "nombre": marca["nombre"], "success": True}
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.post("/api/modelos")
def crear_modelo(modelo: dict, admin = Depends(get_current_admin)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            INSERT INTO modelos (marca_id, nombre, descripcion) 
            VALUES (%s, %s, %s) RETURNING id
        """, (modelo["marca_id"], modelo["nombre"], modelo.get("descripcion", "")))
        new_id = cursor.fetchone()["id"]
        conn.commit()
        return {"id": new_id, "success": True}
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

# ============================================
# ENDPOINTS - USUARIOS
# ============================================

@app.get("/api/usuarios")
def get_usuarios(admin = Depends(get_current_admin)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("""
            SELECT id, email, nombre, rol, activo
            FROM usuarios ORDER BY created_at DESC
        """)
        return cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

@app.post("/api/usuarios")
def crear_usuario(usuario: UsuarioCreate, admin = Depends(get_current_admin)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT id FROM usuarios WHERE email = %s", (usuario.email,))
        if cursor.fetchone():
            raise HTTPException(status_code=400, detail="El email ya está registrado")
        
        hashed_password = hash_password(usuario.password)
        
        cursor.execute("""
            INSERT INTO usuarios (email, nombre, rol, password_hash)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (usuario.email, usuario.nombre, usuario.rol, hashed_password))
        
        new_id = cursor.fetchone()["id"]
        conn.commit()
        
        return {"success": True, "id": new_id, "message": "Usuario creado correctamente"}
    except psycopg2.Error as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        cursor.close()
        conn.close()

@app.put("/api/usuarios/{id_usuario}")
def actualizar_usuario(id_usuario: str, usuario: UsuarioCreate, admin = Depends(get_current_admin)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT id FROM usuarios WHERE id = %s", (id_usuario,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
        hashed_password = hash_password(usuario.password) if usuario.password else None
        
        if hashed_password:
            cursor.execute("""
                UPDATE usuarios SET email = %s, nombre = %s, rol = %s, password_hash = %s
                WHERE id = %s
            """, (usuario.email, usuario.nombre, usuario.rol, hashed_password, id_usuario))
        else:
            cursor.execute("""
                UPDATE usuarios SET email = %s, nombre = %s, rol = %s
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
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE usuarios SET activo = FALSE WHERE id = %s", (id_usuario,))
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
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE usuarios SET activo = TRUE WHERE id = %s", (id_usuario,))
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
# ENDPOINTS - RETIRAR EQUIPO
# ============================================

@app.put("/api/equipos/retirar")
def retirar_equipo(request: RetirarEquipoRequest, admin = Depends(get_current_admin)):
    conn = get_db()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT id, estado, imei FROM equipos WHERE id = %s", (request.equipo_id,))
        equipo = cursor.fetchone()
        
        if not equipo:
            raise HTTPException(status_code=404, detail="Equipo no encontrado")
        
        if equipo["estado"] != "disponible":
            raise HTTPException(status_code=400, detail=f"El equipo no está disponible (estado: {equipo['estado']})")
        
        cursor.execute("""
            UPDATE equipos 
            SET estado = 'retirado', 
                observaciones = COALESCE(observaciones, '') || ' | RETIRADO: ' || %s || ' - ' || NOW()::text
            WHERE id = %s RETURNING id
        """, (request.razon, request.equipo_id))
        
        conn.commit()
        
        return {"success": True, "message": f"Equipo {equipo['imei']} retirado correctamente. Razón: {request.razon}"}
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