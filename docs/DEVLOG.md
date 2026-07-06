## ==아직 하나도 다듬지 않고 그냥 생각나는대로 쓰는중==

## 목차

### [[#0. 프로젝트 구조]]
### [[#1. 개요]]
### [[#2. 환경 설정]]
### [[#3. 데이터]]
### [[4. Vanilla RAG]]

---
## 0. 프로젝트 구조

``` text
ComIn-Project/
|--
```
---
## 1. 개요

후에 langchain, langgraph를 적용시키는 프로젝트로 발전시키려면 할 수 있는게 많은 데이터를 고르는게 중요하다고 생각했다.
어떤 데이터를 사용하면 좋을지 고민하다가 나온 아이디어는 두 개였다.
1. arxiv api를 사용해서 논문을 외부 데이터로 삼는 것.
2. DART 공시 api와 네이버 뉴스 api를 사용해서 기업의 다양한 정보를 찾는 것.
우선 첫 번째 아이디어에 대해 좀 더 설명해보자면, 내 관심 분야인 NLP 관련 논문들을 외부 지식으로 삼아서 사용자가 특정 개념에 대해 질의하면 해당 개념에 관한 논문들을 찾아서 요약해 보여주는 시스템을 만들려고 했다. 하지만 나중에 langgraph를 사용해 분기를 만들거나 에이전트를 만들어서 사용할 만한 것들이 거의 없을 것 같아서 2번으로 넘어가기로 했다.
두 번째 아이디어는 많은 기업들의 정보를 이용해서 에이전트를 구성할 때에도 사용할 아이디어가 많을 것 같았다. 예를 들어 특정 분야의 기업들을 뽑아준다거나, 특정 기업의 실적, 재무제표, 주식 등 다양한 특화 기능을 가진 에이전트를 만들 수 있을 거라고 생각했다. 하지만 이런 내용들은 이미 다양한 증권사의 앱에서 제공되는 기능이라고 생각이 들었고, 어떤 점을 추가해야 기존의 기능들과 차별점을 둘 수 있을지 고민됐다. 취준생이라는 점을 생각했을 때 기업들의 채용 공고들도 데이터로 넣어 기능을 개발한다면 좋은 차별점이 될 수 있을거라 생각했다.
그래서 이 아이디어를 채택하여 프로젝트를 진행해보려고 한다.

---
## 2. 환경 설정

\- 가상환경 세팅
`python3.10 -m venv .venv310

\- 깃 연동
이미 연동된 레포지토리가 있는 디렉토리가 있어서 그곳에서 작업했다.
이번에 작업할 때는 새로운 브랜치를 만들어서 작업하고 메인에 합치는 방식을 시도해보려고 생각중이다.
내 개인 레포지토리에도 기록을 남기고싶어서 방법을 찾아봤다.
하나의 로컬 레포에 두 개의 remote를 등록해서 같은 커밋을 양쪽에 push하면 된다고 해서 시도해보려 한다.
우선 개인 레포지토리를 하나 만들고, 작업중인 디렉토리에서 `personal`이라는 이름으로 개인 레포지토리를 연결했다.
그리고 personal 레포에 기존 부트캠프 레포지토리의 내용을 push해서 동일한 상태로 맞춰줬다.
``` bash
git remote add personal <개인 레포 url> # 내 개인 레포지토리 추가
git remote -v # 부트캠프, 개인 레포지토리 확인

git push personal main # 개인 레포에 커밋 push해서 동일 상태로 맞추기
```

이제 작업을 하고 커밋을 한 뒤에 부트캠프 레포와 개인 레포에 각각 푸시를 하면 될 것 같다.

``` bash
git add 파일들 # 작업한 파일 stage 상태로 변경
git commit -m "message" # 파일 변경 내용 확정
git push origin main # 부트캠프 레포에 변경된 내용 업로드
git push personal main # 개인 레포에 변경된 내용 업로드
```

--> 개인 레포를 새로 만들어서 옮겼다. 이제 여기에만 커밋할 예정이다.

\- 사용한 API 사이트
[openDART 사이트](https://opendart.fss.or.kr/)
[네이버 API HUB](https://www.ncloud.com/product/applicationService/naverApiHub)

\- `.env`, `config.py` 파일 설정
발급받은 API 키들을 `.env` 파일에 넣고 `config.py` 파일에서 키들을 변수에 넣은 뒤, 키들에 제대로 값이 들어가있는지 확인하는 함수를 만들었다.

\- 프로젝트 세팅 방법
git clone <레포>
pip3 install uv

처음 만들 때
	uv init
	uv add -r requirements.txt
깃에서 가져왔을 때
	uv sync

.env 파일에 키 넣기
uv run data/config.py -> 기본 설정
uv run data/corp_code.py -> 회사 고유코드 매핑 테이블
uv run data/collect_data.py

---
## 3. 데이터

우처음부터 많은 데이터를 가져와 만들지 않고, 간단한 데이터만 가져온 뒤 완성된 시스템을 만들려고 한다. 제대로 동작이 되는 것을 확인하고, 여러 데이터들을 더 추가하며 기능 추가를 진행할 것이다.

그럼 첫번째로 DART API를 사용해서 데이터를 가져와보자.

### \- 고유번호 매핑 테이블 생성

DART API를 사용해서 특정 회사의 공시 정보를 가져오기 위해서는 회사의 고유번호를 입력해야한다. 그래서 회사명을 입력하면 해당 회사의 고유번호로 매핑해주는 테이블이 필요하다. 매핑된 테이블은 로컬 sqlite 저장소에 저장한다.
sqlite를 사용하는 이유는 크게 두 가지가 있다.
1. 한 번만 데이터를 저장해놓으면 쓰기 작업이 거의 없다.
2. 아직 개인 프로젝트라 원격 접속이 필요하지 않다.

각 회사별 고유번호가 담겨있는 zip파일은 DART API를 통해 가져올 수 있다.
요청 url은 `https://opendart.fss.or.kr/api/corpCode.xml`이다.

``` python
def build_corp_code_db():
    """
    전체 고유번호가 들어있는 zip 파일을 다운받아 파싱 후 sqlite에 저장하는 함수
    """
    check_keys()
    
	# 1. 전체 고유번호가 담긴 zip 다운로드
	response = requests.get(CORP_CODE_URL, params={"crtfc_key":DART_API_KEY}, timeout=30)
	
	# 2. zip 안의 CORPCODE.xml 파싱
	with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
		xml_name = zf.namelist()[0]
		with zf.open(xml_name) as f:
			tree = ET.parse(f)
	root = tree.getroot()
	
	rows = []
	    for item in root.iter("list"): # 노드들중 "list" 태그를 가진 노드만 사용
	      # 고유번호, 정식명칭, 종목코드, 최종변경일자 파싱 후 추가
	      rows.append(
	          (item.findtext("corp_code") or "").strip(),
	          (item.findtext("corp_name") or "").strip(),
	          (item.findtext("stock_code") or "").strip(),
	          (item.findtext("modify_date") or "").strip(),
	      )  
```

위 코드를 통해 전체 고유번호 zip을 받아와 고유번호가 들어있는 `CORPCODE.xml`을 파싱한다.
`xml` 파일은 계층적인 구조를 이루고 있는데 `root.iter()`를 하면 `root`에서 시작해서 하위 노드까지 재귀적으로 방문할 수 있다. iter() 안에 아무 하이퍼파라미터를 주지 않으면 모든 노드를 가져오고, `root.iter("list")`와 같이 작성하면 노드 중 `"list"` 태그를 가진 노드들만 가져온다. 

![[공시정보_API_응답결과.png]]

이 데이터에서는 `"list"` 태그 안에 고유번호, 회사명칭과 같은 정보들이 들어있다.

값들을 rows에 담았으니 이제 sqlite에 저장할 차례이다.

``` python
def build_corp_code_db():
    """
    전체 고유번호가 들어있는 zip 파일을 다운받아 파싱 후 sqlite에 저장하는 함수
    """
	
	...
	
	conn = sqlite3.connect(DB_PATH)
	cur = conn.cursor()
	cur.execute("DROP TABLE IF EXISTS corp") # 이미 corp 테이블이 존재하면 테이블 드랍
	cur.execute( # 테이블 생성
		"""
		CREATE TABLE corp (
			corp_code TEXT,
			corp_name TEXT,
			stock_code TEXT,
			modify_date TEXT
		)
		"""
	)
	cur.executemany("INSERT INTO corp VALUES (?, ?, ?, ?)", rows)
	cur.execute("CREATE INDEX idx_cor_name ON corp(corp_name)") # 회사명으로 검색했을 때 속도를 빠르게 하기 위해 인덱스로 설정
	conn.commit()
	conn.close()
```

이렇게 `build_corp_code_db()` 함수를 통해 회사명-고유번호 매핑 테이블을 만들었다. 이제 회사명이 들어오면 테이블을 통해 고유번호를 반환해주는 함수를 만들면 된다.

/// 회사명을 테이블에 저장되어있는 corp_name과 정확히 일치되지 않게 입력하면 어떻게 처리?

```python
# 회사명을 받아 고유코드, 정식회사명칭, 종목코드를 반환하는 함수
def find_corp_code(corp_name: str) -> dict:
    """
    회사명으로 corp_code를 조회한다.
    같은 이름이 여러 개 존재할 수 있는데, 이 때는 상장사를 우선 반환한다.
    (stock_code가 있는 회사)
    """
    if not os.path.exists(DB_PATH):
        raise RuntimeError(
            f"{DB_PATH}가 없습니다. 먼저 매핑 테이블을 만들어주세요."
        )

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 회사명과 일치하는 결과가 있으면 가져오기
    cur.execute(
        "SELECT corp_code, corp_name, stock_code FROM corp WHERE corp_name = ?",
        (corp_name, ) # ,을 넣어주지 않으면 튜플이라고 인식하지 못하고 input sequence로 인식돼서 만약 corp_name이 "삼성전자"이면 4개의 값으로 인식된다.
    )
    matches = cur.fetchall()
    conn.close()

    # 결과값이 없을 때
    if not matches:
        return None

    listed = [match for match in matches if match[2]] # 종목코드가 있는 회사
    corp_code, name, stock_code = listed[0] if listed else matches[0] # 종목코드 있으면 상장사, 없으면 그냥 결과값에서 반환

    return {"corp_code":corp_code, "corp_name":name, "stock_code":stock_code}
```

이제 매핑 테이블을 만들었으니 API를 통해 데이터를 가져올 수 있다.

### \- 데이터 수집

데이터 수집 파이썬 파일은 크게 세 가지로 나뉘어있다.
1. 공시 정보를 가져오는 함수
2. 네이버 뉴스를 가져오는 함수
3. 공시 정보와 네이버 뉴스 정보를 합쳐서 반환하는 함수

.DART API를 사용해 공시정보를 가져오는 함수를 만들어보자.
``` python

```

다음은 특정 회사의 최근 네이버 뉴스 기사를 가져오는 함수.
``` python

```

이제 이 두 정보를 합쳐서 반환하는 함수.
``` python

```

잘 작동하는지 확인해보자.
``` json
{
      "report_nm": "임원ㆍ주요주주특정증권등소유상황보고서",
      "rcept_no": "20260630000993",
      "rcept_dt": "20260630",
      "flr_nm": "심재현"
    },
{
      "title": "\"올해 폴더블 스마트폰 패널 2750만대 전망...24% ↑\"",
      "description": "출하량 증가와 함께 애플과 삼성전자의 프리미엄 인폴드 제품 확대가 고가 패널 비중을 끌어올릴 것으로 분석된다. 카운터포인트리서치는 올해 애플의 첫 폴더블 아이폰 출시 가능성을 올해 시장 판도를 바꿀 핵심... ",
      "link": "https://n.news.naver.com/mnews/article/031/0001040138?sid=105",
      "pubDate": "Mon, 06 Jul 2026 17:15:00 +0900"
    }
```
많이 생략하긴 했지만 이런 식으로 잘 작동하는 것을 확인할 수 있다.


