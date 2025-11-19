# --- Cập nhật lại hàm upload trong main.py ---

@app.post("/api/v1/upload")
async def upload_file(file: UploadFile = File(...)):
    """
    API Upload file chuẩn (Sử dụng Multipart/Form-data)
    Không cần gửi filename qua Header nữa.
    """
    if not RUNNINGHUB_API_KEY:
        raise HTTPException(500, "Server chưa có API Key.")
        
    try:
        # 1. Đọc nội dung file từ client gửi lên
        file_content = await file.read()
        
        # 2. Lấy tên file tự động từ object UploadFile
        filename = file.filename
        
        # 3. Tạo form-data để đẩy sang RunningHub
        # Cấu trúc: (tên_file, nội_dung_bytes, loại_mime)
        files = {'file': (filename, file_content, file.content_type or 'image/png')} 
        data = {'apiKey': RUNNINGHUB_API_KEY, 'fileType': 'image'}
        
        # 4. Gửi sang RunningHub
        res = await client.post(RUNNINGHUB_URLS["upload"], files=files, data=data)
        res_data = res.json()
        
        if res_data.get("code") != 0:
             raise HTTPException(400, f"Upload lỗi từ RunningHub: {res_data.get('msg')}")
             
        return res_data.get("data")
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(500, f"Lỗi Upload Server: {str(e)}")
