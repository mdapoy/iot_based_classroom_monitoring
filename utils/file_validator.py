from fastapi import UploadFile, HTTPException


def validate_xlsx(file: UploadFile):
    name = file.filename.lower()
    if not (name.endswith(".xlsx") or name.endswith(".xls")):
        raise HTTPException(
            status_code=400,
            detail="File harus format XLSX"
        )


def validate_csv(file: UploadFile):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=400,
            detail="File harus format CSV"
        )
