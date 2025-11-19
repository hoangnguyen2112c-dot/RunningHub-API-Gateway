import os
import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
# THÊM IMPORT: Để lấy thời gian chính xác
from datetime import datetime, timezone, timedelta 

# ==========================================
#        CẤU HÌNH SERVER (LẤY TỪ RENDER)
# ==========================================

# 1. API Key RunningHub của Admin (Bạn)
RUNNINGHUB_API_KEY = os.environ.get("RUNNINGHUB_API_KEY")

# 2. Link Google Apps Script (Database quản lý User)
USER_DB_URL = os.environ.get("USER_DB_URL")

# 3. Cấu hình Workflow ID bí mật (Vẫn giữ để tham khảo)
PRESET_CONFIGS = {
    "Normal (24G)": {
        "id": "1984294242724036609", 
        "prompt_id": "416", 
        "image_id": "284", 
        "strength_id": "134", 
        "show_strength": True
    },
    "Upscale (24G)": {
        "id": "1981382064639492097", 
        "prompt_id": "45", 
        "image_id": "59", 
        "strength_id": "", 
        "show_strength": False
    },
}

RUNNINGHUB_URLS = {
    "create": "https://www.runninghub.cn/task/openapi/create",
    "ai_app_run": "https://www.runninghub.ai/task/openapi/ai-app/run",
    "status": "https://www.runninghub.ai/task/openapi/status",
    "outputs": "https://www.runninghub.ai/task/openapi/outputs",
    "upload": "https://www.runninghub.ai/task/openapi/upload",
}

# ==========================================
#              KHỞI TẠO APP
# ==========================================

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = httpx.AsyncClient(timeout=60.0, verify=False, follow_redirects=True)

# --- CÁC MODEL DỮ LIỆU ---

class LoginRequest(BaseModel):
    username: str
    password: str

class CreateTaskRequest(BaseModel):
    username: str
    password: str
    workflow_id: str  
    prompt_id: str  
    image_id: str  
    strength_id: Optional[str] = None
    
    gpu_mode: str
    prompt_text: str
    img_path: str
    strength: Optional[float] = None

# ==========================================
#             HÀM HỖ TRỢ (LOGIC)
# ==========================================

# SỬA: Thêm task_id và timestamp vào tham số
async def check_and_deduct(username, password, action="login", task_id: Optional[str] = None, timestamp: Optional[str] = None):
    """Gọi Google Apps Script để check user, trừ tiền và ghi log Task"""
    if not USER_DB_URL:
        raise HTTPException(500, "Server chưa cấu hình USER_DB_URL (Google Script).")
    
    try:
        # Xây dựng Payload
        payload = {"action": action, "username": username, "password": password}
        
        # THÊM LOGIC: Ghi Task ID và Timestamp khi action là "deduct"
        if action == "deduct":
            payload["taskId"] = task_id or "N/A"
            payload["timestamp"] = timestamp or "N/A"
        
        # Gọi sang Google Sheet
        res = await client.post(USER_DB_URL, json=payload)
        data = res.json()
        
        if not data.get("success"):
            raise HTTPException(401, data.get("message", "Lỗi xác thực"))
            
        if action == "login" and data.get("credits", 0) <= 0:
             raise HTTPException(402, "Tài khoản đã hết lượt chạy! Vui lòng liên hệ Admin.")
             
        return data
        
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(500, f"Lỗi kết nối Database: {str(e)}")

# ==========================================
#              API ENDPOINTS
# ==========================================

@app.post("/api/v1/login")
async def login_endpoint(req: LoginRequest):
    """API Đăng nhập & Kiểm tra số dư"""
    return await check_and_deduct(req.username, req.password, "login")

@app.post("/api/v1/workflow/create")
async def create_task(req: CreateTaskRequest):
    """API Tạo Task (Có trừ tiền và ghi log Task ID)"""
    
    # BƯỚC 1: Kiểm tra đăng nhập & Số dư TRƯỚC khi chạy
    await check_and_deduct(req.username, req.password, "login")

    # BƯỚC 2: Xây dựng Node Info (Mapping tham số vào đúng Node ID)
    node_info = []
    if req.prompt_text and req.prompt_id:
        node_info.append({"nodeId": req.prompt_id, "fieldName": "text", "fieldValue": req.prompt_text})
    if req.strength is not None and req.strength_id:
        node_info.append({"nodeId": req.strength_id, "fieldName": "guidance", "fieldValue": req.strength})
    if req.img_path and req.image_id:
        node_info.append({"nodeId": req.image_id, "fieldName": "image", "fieldValue": req.img_path})

    payload = {"workflowId": req.workflow_id, "nodeInfoList": node_info}

    if not RUNNINGHUB_API_KEY:
        raise HTTPException(500, "Server chưa có API Key RunningHub.")

    full_payload = {**payload, "apiKey": RUNNINGHUB_API_KEY}
    
    try:
        rh_res = await client.post(RUNNINGHUB_URLS["create"], json=full_payload)
        rh_data = rh_res.json()
        
        if rh_data.get("code") != 0:
            raise Exception(rh_data.get("msg"))
        
        task_id = rh_data.get("data", {}).get("taskId")
        
        # THÊM LOGIC: Lấy ngày giờ hiện tại (GMT+7 cho Việt Nam)
        vietnam_time = timezone(timedelta(hours=7))
        current_time = datetime.now(vietnam_time).strftime("%Y-%m-%d %H:%M:%S")

        # BƯỚC 4: Gọi Google Sheet để TRỪ TIỀN & GHI LOG Task ID, Time
        await check_and_deduct(
            req.username, 
            req.password, 
            "deduct", 
            task_id=task_id, 
            timestamp=current_time
        )
        
        return {"taskId": task_id}
        
    except Exception as e:
        raise HTTPException(500, f"Lỗi RunningHub: {str(e)}")

# --- API UPLOAD ---
@app.post("/api/v1/upload")
async def upload_file(file: UploadFile = File(...)):
    """API Upload file chuẩn (Sử dụng Multipart/Form-data)"""
    if not RUNNINGHUB_API_KEY:
        raise HTTPException(500, "Server chưa có API Key.")
        
    try:
        file_content = await file.read()
        filename = file.filename
        
        files = {'file': (filename, file_content, file.content_type or 'image/png')} 
        data = {'apiKey': RUNNINGHUB_API_KEY, 'fileType': 'image'}
        
        res = await client.post(RUNNINGHUB_URLS["upload"], files=files, data=data)
        res_data = res.json()
        
        if res_data.get("code") != 0:
             raise HTTPException(400, f"Upload lỗi từ RunningHub: {res_data.get('msg')}")
             
        return res_data.get("data")
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(500, f"Lỗi Upload Server: {str(e)}")

# --- CÁC API TRA CỨU (PROXY) ---

@app.get("/api/v1/task/status/{task_id}")
async def get_status(task_id: str):
    """Kiểm tra trạng thái Task"""
    res = await client.post(
        RUNNINGHUB_URLS["status"], 
        json={"taskId": task_id, "apiKey": RUNNINGHUB_API_KEY}
    )
    return res.json()

@app.get("/api/v1/task/outputs/{task_id}")
async def get_outputs(task_id: str):
    """Lấy kết quả Task"""
    res = await client.post(
        RUNNINGHUB_URLS["outputs"], 
        json={"taskId": task_id, "apiKey": RUNNINGHUB_API_KEY}
    )
    return res.json()

@app.on_event("shutdown")
async def app_shutdown():
    await client.aclose()
