import os
import httpx
from fastapi import FastAPI, HTTPException, Body, Path, UploadFile, File, Form, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

# --- CẤU HÌNH BÍ MẬT (CHỈ CHẠY TRÊN SERVER RENDER) ---

# Đọc API Key của RunningHub từ biến môi trường trên Render.
RUNNINGHUB_API_KEY = os.environ.get("RUNNINGHUB_API_KEY")

# Workflow IDs BÍ MẬT được bảo mật trên Server
PRESET_CONFIGS = {
    "Normal (1984...)": {"id": "1984294242724036609", "prompt_id": "416", "image_id": "284", "strength_id": "134", "show_strength": True},
    "Vip (1989...)": {"id": "1989957298149838849", "prompt_id": "416", "image_id": "284", "strength_id": "134", "show_strength": True},
    "Upscale (1981...)": {"id": "1981382064639492097", "prompt_id": "45", "image_id": "59", "strength_id": "", "show_strength": False},
}

# RunningHub API URLs (dùng các URL AI vì đó là các dịch vụ trả phí)
RUNNINGHUB_URLS = {
    "create": "https://www.runninghub.cn/task/openapi/create",
    "status": "https://www.runninghub.ai/task/openapi/status",
    "outputs": "https://www.runninghub.ai/task/openapi/outputs",
    "upload": "https://www.runninghub.ai/task/openapi/upload",
    "account_status": "https://www.runninghub.ai/uc/openapi/accountStatus",
}

# --- KHỞI TẠO FASTAPI ---
app = FastAPI(
    title="RunningHub Secure API Gateway",
    description="API trung gian bảo mật Workflow ID và API Key.",
    version="1.0.0"
)

# Cho phép client (app_gradio_client.py) gọi đến API này
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Models (Cấu trúc dữ liệu nhận từ Client) ---
class NodeInfo(BaseModel):
    nodeId: str
    fieldName: str
    fieldValue: Any

class CreateWorkflowRequest(BaseModel):
    # CLIENT KHÔNG GỬI WORKFLOW ID, CHỈ GỬI TÊN PRESET!
    preset_name: str
    gpu_mode: str
    prompt_text: Optional[str] = None
    img_path: Optional[str] = None
    strength: Optional[float] = None

# Client HTTP dùng chung (cho việc gọi RunningHub)
client = httpx.AsyncClient(timeout=60.0, verify=False)


# --- Hàm chính để gọi RunningHub (SERVER LOGIC) ---
async def call_runninghub_api(url_key: str, payload: dict):
    if not RUNNINGHUB_API_KEY:
        raise HTTPException(status_code=500, detail="RUNNINGHUB_API_KEY chưa được cấu hình trên Server.")
        
    full_payload = {**payload, "apiKey": RUNNINGHUB_API_KEY}
    
    try:
        response = await client.post(RUNNINGHUB_URLS[url_key], json=full_payload)
        response.raise_for_status()
        data = response.json()
        
        if data.get("code") != 0:
            raise HTTPException(
                status_code=400, 
                detail=f"RunningHub API báo lỗi: {data.get('msg', 'Lỗi không rõ')}"
            )
        return data.get("data")
        
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=f"Lỗi từ RunningHub: {exc.response.text}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail=f"Không thể kết nối đến RunningHub: {str(exc)}")

# --- API ENDPOINTS CỦA SERVER ---

@app.post("/api/v1/workflow/create")
async def create_workflow_task(request: CreateWorkflowRequest):
    """
    Nhận yêu cầu từ App Khách, tự động chèn Workflow ID, Node IDs và API Key.
    """
    preset_config = PRESET_CONFIGS.get(request.preset_name)
    if not preset_config:
        raise HTTPException(status_code=400, detail="Preset không hợp lệ.")
        
    workflow_id = preset_config["id"]
    prompt_id = preset_config["prompt_id"]
    img_id = preset_config["image_id"]
    strength_id = preset_config["strength_id"]

    node_info_list = []
    
    # 1. Prompt (Text)
    if request.prompt_text and prompt_id:
        node_info_list.append({"nodeId": prompt_id, "fieldName": "text", "fieldValue": request.prompt_text})

    # 2. Strength (Guidance)
    if request.strength is not None and strength_id:
        node_info_list.append({"nodeId": strength_id, "fieldName": "guidance", "fieldValue": request.strength})
        
    # 3. Image Path (Đã được upload trước đó)
    if request.img_path and img_id:
        node_info_list.append({"nodeId": img_id, "fieldName": "image", "fieldValue": request.img_path})

    # --- TẠO PAYLOAD GỐC GỬI ĐẾN RUNNINGHUB ---
    payload = {
        "workflowId": workflow_id,
        "nodeInfoList": node_info_list
    }
    
    # --- XỬ LÝ GPU MODE ---
    if request.gpu_mode == "Plus (48G VRAM - Cao Cấp)":
        payload["gpuType"] = "plus"
        payload["taskType"] = "plus"
        payload["useVip"] = True

    # Gọi RunningHub API
    task_data = await call_runninghub_api("create", payload)
    
    # Trả về task ID cho client (app_gradio_client.py)
    return {"taskId": task_data.get("taskId")}

@app.post("/api/v1/upload")
async def upload_file(fileType: str = Form(...), file: UploadFile = File(...)):
    """
    Endpoint này nhận file từ Client, tự thêm API Key, và chuyển tiếp lên RunningHub.
    """
    if not RUNNINGHUB_API_KEY:
        raise HTTPException(status_code=500, detail="RUNNINGHUB_API_KEY chưa được cấu hình.")

    files_to_send = {'file': (file.filename, await file.read(), file.content_type)}
    data_to_send = {'apiKey': RUNNINGHUB_API_KEY, 'fileType': fileType}
    
    try:
        response = await client.post(RUNNINGHUB_URLS["upload"], files=files_to_send, data=data_to_send)
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise HTTPException(status_code=400, detail=f"RunningHub (Upload) báo lỗi: {data.get('msg')}")
        
        return data.get("data")
        
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi khi upload file: {str(exc)}")

@app.get("/api/v1/task/status/{task_id}")
async def get_task_status(task_id: str):
    return await call_runninghub_api("status", {"taskId": task_id})

@app.get("/api/v1/task/outputs/{task_id}")
async def get_task_outputs(task_id: str):
    return await call_runninghub_api("outputs", {"taskId": task_id})

@app.get("/api/v1/account/status")
async def get_account_status():
    return await call_runninghub_api("account_status", {})

@app.on_event("shutdown")
async def app_shutdown():
    await client.aclose()