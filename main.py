from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.v1.router import api_router
from core.config import settings

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",               # local
        "https://platform-demo.prsti.ai",      # API domain
        "https://seediq.prsti.ai",
	"https://seediq-test.prsti.ai"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




app.include_router(api_router, prefix="/api/v1")

#if __name__ == "__main__":
 #   import uvicorn
  #  uvicorn.run("main:app", host="127.0.0.1", port=8002, reload=False)
