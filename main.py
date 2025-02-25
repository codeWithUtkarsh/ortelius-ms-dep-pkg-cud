import json
import os
from typing import Optional

import requests
from fastapi import Body, FastAPI, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import create_engine

# Init Globals
service_name = 'ortelius-ms-dep-pkg-cud'

# Init FastAPI
app = FastAPI()

# Init db connection
db_host = os.getenv("DB_HOST", "localhost")
db_name = os.getenv("DB_NAME", "postgres")
db_user = os.getenv("DB_USER", "postgres")
db_pass = os.getenv("DB_PASS", "postgres")
db_port = os.getenv("DB_PORT", "5432")
validateuser_url = os.getenv("VALIDATEUSER_URL", "http://localhost:5000")

url = requests.get('https://raw.githubusercontent.com/pyupio/safety-db/master/data/insecure_full.json')
safety_db = json.loads(url.text)

engine = create_engine("postgresql+psycopg2://" + db_user + ":" + db_pass + "@" + db_host + "/" + db_name)

# health check endpoint

class StatusMsg(BaseModel):
    status: str
    service_name: Optional[str] = None


@app.get("/health",
         responses={
             503: {"model": StatusMsg,
                   "description": "DOWN Status for the Service",
                   "content": {
                       "application/json": {
                           "example": {"status": 'DOWN'}
                       },
                   },
                   },
             200: {"model": StatusMsg,
                   "description": "UP Status for the Service",
                   "content": {
                       "application/json": {
                           "example": {"status": 'UP', "service_name": service_name}
                       }
                   },
                   },
         }
         )
async def health(response: Response):
    try:
        with engine.connect() as connection:
            conn = connection.connection
            cursor = conn.cursor()
            cursor.execute('SELECT 1')
            if cursor.rowcount > 0:
                return {"status": 'UP', "service_name": service_name}
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": 'DOWN'}

    except Exception as err:
        print(str(err))
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": 'DOWN'}

# validate user endpoint

def example(filename):
    text = ''
    with open(filename, 'r') as f:
        text = f.read()
    return text

class Message(BaseModel):
    detail: str


@app.post('/msapi/deppkg/cyclonedx',
          response_model=Message,
          responses={
              401: {"model": Message,
                    "description": "Authorization Status",
                    "content": {
                        "application/json": {
                            "example": {"detail": "Authorization failed"}
                        },
                    },
                    },
              500: {"model": Message,
                    "description": "SQL Error",
                    "content": {
                        "application/json": {
                            "example": {"detail": "SQL Error: 30x"}
                        },
                    },
                    },
              200: {
                  "model": Message,
                  "description": "Success Message",
                  "content": {
                      "application/json": {
                          "example": {"detail": "Component updated successfully"}
                      }
                  },
              },
          }
          )
async def cyclonedx(request: Request, response: Response, compid: int, cyclonedx_json: dict = Body(...,example=example('cyclonedx.json'),description='JSON output from running CycloneDX')):
    try:
        result = requests.get(validateuser_url + "/msapi/validateuser", cookies=request.cookies)
        if (result is None):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization Failed")

        if (result.status_code != status.HTTP_200_OK):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization Failed status_code=" + str(result.status_code))
    except Exception as err:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization Failed:" + str(err)) from None

    components_data = []
    components = cyclonedx_json.get('components', [])

    # Parse CycloneDX BOM for licenses
    bomformat = 'license'
    for component in (components):
        packagename = component.get('name')
        packageversion = component.get('version', '')
        summary = ''
        license_url = ''
        license_name = ''
        licenses = component.get('licenses')
        if (licenses):
            license_name = licenses[0].get('license').get('name', '')
            license_url = 'https://spdx.org/licenses/' + license_name + '.html'
        component_data = (compid, packagename, packageversion, bomformat, license_name, license_url, summary)
        components_data.append(component_data)

    return saveComponentsData(response, compid, bomformat, components_data)


@app.post('/msapi/deppkg/safety',
          response_model=Message,
          responses={
              401: {"model": Message,
                    "description": "Authorization Status",
                    "content": {
                        "application/json": {
                            "example": {"detail": "Authorization failed"}
                        },
                    },
                    },
              500: {"model": Message,
                    "description": "SQL Error",
                    "content": {
                        "application/json": {
                            "example": {"detail": "SQL Error: 30x"}
                        },
                    },
                    },
              200: {
                  "model": Message,
                  "description": "Success Message",
                  "content": {
                      "application/json": {
                          "example": {"detail": "Component updated successfully"}
                      }
                  },
              },
          }
          )
async def safety(request: Request, response: Response, compid: int, safety_json: list = Body(...,example=example('safety.json'),description='JSON output from running safety')):
    result = requests.get(validateuser_url + "/msapi/validateuser", cookies=request.cookies)
    if (result is None):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization Failed")

    if (result.status_code != status.HTTP_200_OK):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authorization Failed status_code=" + str(result.status_code))

    components_data = []
    bomformat = 'cve'
    for component in (safety_json):
        packagename = component[0]  # name
        packageversion = component[2]  # version
        summary = component[3]
        safety_id = component[4]  # cve id
        cve_url = ''
        cve_name = safety_id
        cve_detail = safety_db.get(packagename, None)
        if (cve_detail is not None):
            for cve in cve_detail:
                if (cve['id'] == 'pyup.io-' + safety_id):
                    cve_name = cve['cve']
                    if (cve_name.startswith('CVE')):
                        cve_url = 'https://nvd.nist.gov/vuln/detail/' + cve_name
                    break

        component_data = (compid, packagename, packageversion, bomformat, cve_name, cve_url, summary)
        components_data.append(component_data)
    return saveComponentsData(response, compid, bomformat, components_data)

def saveComponentsData(response, compid, bomformat, components_data):
    try:
        if len(components_data) == 0:
            return {"detail": "components not updated"}

        with engine.connect() as connection:
            conn = connection.connection
            conn.set_session(autocommit=False)
            cursor = conn.cursor()
            records_list_template = ','.join(['%s'] * len(components_data))

            # delete old licenses
            sql = 'DELETE from dm_componentdeps where compid=%s and deptype=%s'
            params = (compid, bomformat,)
            cursor.execute(sql, params)

            # insert into database
            sql = 'INSERT INTO dm_componentdeps(compid, packagename, packageversion, deptype, name, url, summary) VALUES {}'.format(records_list_template)

            cursor.execute(sql, components_data)

            rows_inserted = cursor.rowcount
            # Commit the changes to the database
            conn.commit()
            if rows_inserted > 0:
                response.status_code = status.HTTP_201_CREATED
                return {"detail": "components updated succesfully"}

        return {"detail": "components not updated"}

    except HTTPException:
        raise
    except Exception as err:
        print(str(err))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(err)) from None
