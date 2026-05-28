from fastapi import UploadFile, HTTPException

def validate_csv(file: UploadFile):

    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="File harus format CSV"
        )
