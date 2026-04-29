import pandas as pd
from repositories.supabase_client import supabase

def process_csv(file):

    df = pd.read_csv(file)

    df.columns = df.columns.str.lower().str.replace(" ", "_")

    df[["jam_mulai", "jam_selesai"]] = df["shift"].str.split(" - ", expand=True)

    df = df.drop(columns=["shift"])

    df = df.fillna("")

    data = df.to_dict(orient="records")

    supabase.table("jadwal_kuliah").insert(data).execute()

    return {
        "status": "success",
        "rows_inserted": len(data)
    }