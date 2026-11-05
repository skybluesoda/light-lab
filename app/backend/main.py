from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from experiments import SettingsDocument,json_bytes
from service import RenderService,RenderRequest,default_request,runtime_status,ROOT


def create_app(service=None):
    service=service or RenderService()
    @asynccontextmanager
    async def lifespan(app):
        service.start()
        yield
        service.close()
    app=FastAPI(title='LIGHT LAB',lifespan=lifespan,docs_url='/api/docs',redoc_url=None)
    app.state.service=service
    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        # Never echo non-finite JSON values or Python exception objects in errors.
        detail=[{'loc':error.get('loc',[]),'msg':str(error.get('msg','입력을 확인하세요.')),'type':error.get('type','value_error')} for error in exc.errors()]
        return JSONResponse(status_code=422,content={'detail':detail})
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]'])

    @app.middleware('http')
    async def local_requests(request:Request,call_next):
        if request.method in ('POST','DELETE','PUT','PATCH'):
            origin=request.headers.get('origin')
            if origin and origin!=f'{request.url.scheme}://{request.headers.get("host")}':
                return PlainTextResponse('다른 웹사이트에서 실행할 수 없습니다.',status_code=403)
        response=await call_next(request)
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        return response

    @app.get('/api/bootstrap')
    def bootstrap():
        return {'defaults':default_request().model_dump(),'runtime':runtime_status(),
                'example':service.example,'jobs':service.history(),'settings':service.settings(),'benchmarks':service.benchmarks(),
                'comparison_note':'Eevee: 래스터화 + 간접광 근사 / Cycles: 패스트레이싱 · 라이트맵 없음'}

    @app.get('/api/health')
    def health():return {'ok':True,**runtime_status()}

    @app.get('/api/jobs')
    def jobs():return service.history()

    @app.post('/api/jobs',status_code=202)
    def submit(payload:RenderRequest,fresh:bool=False):
        try:return service.submit(payload,force=fresh)
        except RuntimeError as error:raise HTTPException(409,str(error))

    @app.get('/api/jobs/{identifier}')
    def get(identifier:str):
        try:return service.get(identifier)
        except KeyError:raise HTTPException(404,'작업을 찾지 못했습니다.')

    @app.delete('/api/jobs/{identifier}')
    def cancel(identifier:str):
        try:return service.cancel(identifier)
        except KeyError:raise HTTPException(404,'작업을 찾지 못했습니다.')

    @app.get('/api/jobs/{identifier}/images/{engine}.png')
    def image(identifier:str,engine:str):
        try:return FileResponse(service.image(identifier,engine),media_type='image/png')
        except KeyError:raise HTTPException(404,'이 엔진의 결과 이미지가 아직 없습니다.')

    @app.get('/api/jobs/{identifier}/log',response_class=PlainTextResponse)
    def log(identifier:str):
        try:
            service.get(identifier)
            if identifier=='example':return (ROOT/'preview-approx/render.log').read_text(errors='replace')[-50000:]
            path=service.directory(identifier)/'render.log'
            return path.read_text(errors='replace')[-50000:] if path.exists() else '렌더 로그가 아직 없습니다.'
        except KeyError:raise HTTPException(404,'작업을 찾지 못했습니다.')

    @app.get('/api/settings')
    def settings():return service.settings()

    @app.post('/api/settings',status_code=201)
    def save_setting(payload:SettingsDocument):return service.save_setting(payload)

    @app.get('/api/settings/{identifier}/download')
    def download_setting(identifier:str):
        try:
            record=service.setting(identifier)
            return Response(json_bytes({k:record[k] for k in ('format_version','name','request')}),media_type='application/json',
                            headers={'Content-Disposition':f'attachment; filename="light-lab-settings-{identifier[:8]}.json"'})
        except KeyError:raise HTTPException(404,'설정을 찾지 못했습니다.')

    @app.get('/api/jobs/{identifier}/download')
    def download_result(identifier:str):
        try:return Response(service.export_job(identifier),media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="LIGHT-LAB-{identifier[:8]}.zip"'})
        except KeyError:raise HTTPException(404,'작업을 찾지 못했습니다.')
        except (RuntimeError,ValueError,OSError) as error:raise HTTPException(409,str(error))

    @app.get('/api/benchmarks')
    def benchmarks():return service.benchmarks()

    @app.post('/api/benchmarks',status_code=202)
    def start_benchmark(payload:RenderRequest):
        try:return service.start_benchmark(payload)
        except RuntimeError as error:raise HTTPException(409,str(error))

    @app.get('/api/benchmarks/{identifier}')
    def benchmark(identifier:str):
        try:return service.benchmark(identifier)
        except KeyError:raise HTTPException(404,'측정을 찾지 못했습니다.')

    @app.delete('/api/benchmarks/{identifier}')
    def cancel_benchmark(identifier:str):
        try:return service.cancel_benchmark(identifier)
        except KeyError:raise HTTPException(404,'측정을 찾지 못했습니다.')

    @app.get('/api/benchmarks/{identifier}/download')
    def download_benchmark(identifier:str):
        try:return Response(service.benchmark_csv(identifier),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="LIGHT-LAB-benchmark-{identifier[:8]}.csv"'})
        except KeyError:raise HTTPException(404,'측정을 찾지 못했습니다.')

    dist=ROOT/'app/frontend/dist'
    if (dist/'assets').is_dir():app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')

    @app.get('/')
    def index():
        if not (dist/'index.html').exists():raise HTTPException(503,'화면 빌드가 필요합니다.')
        return FileResponse(dist/'index.html',headers={'Cache-Control':'no-cache'})
    return app


app=create_app()
