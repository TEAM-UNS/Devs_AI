# 분야와 스킬 사전 시드 데이터

from dataclasses import dataclass, field

from app.domains.market.enums import TechField


@dataclass(frozen=True)
class FieldSeed:
    code: str
    name: str
    sort_order: int


@dataclass(frozen=True)
class SkillSeed:
    name: str
    category: str
    fields: tuple[TechField, ...]
    aliases: tuple[str, ...] = field(default_factory=tuple)
    is_ambiguous: bool = False
    is_common: bool = False
    cs_aliases: tuple[str, ...] = field(default_factory=tuple)

    def all_aliases(self) -> list[str]:
        excluded = {a.lower() for a in self.cs_aliases}
        seen: dict[str, None] = {}
        for alias in (self.name, *self.aliases):
            key = alias.strip().lower()
            if key and key not in excluded:
                seen.setdefault(key, None)
        return list(seen)

    def all_cs_aliases(self) -> list[str]:
        seen: dict[str, None] = {}
        for alias in self.cs_aliases:
            if alias.strip():
                seen.setdefault(alias.strip(), None)
        return list(seen)


FIELD_CATALOG: tuple[FieldSeed, ...] = (
    FieldSeed(TechField.BACKEND, "백엔드", 10),
    FieldSeed(TechField.FRONTEND, "프론트엔드", 20),
    FieldSeed(TechField.MOBILE, "모바일", 30),
    FieldSeed(TechField.DATA_AI, "데이터/AI", 40),
    FieldSeed(TechField.DEVOPS, "DevOps/인프라", 50),
    FieldSeed(TechField.SECURITY, "보안", 60),
    FieldSeed(TechField.GAME, "게임", 70),
    FieldSeed(TechField.EMBEDDED, "임베디드", 80),
)

_B = TechField.BACKEND
_F = TechField.FRONTEND
_M = TechField.MOBILE
_D = TechField.DATA_AI
_O = TechField.DEVOPS
_S = TechField.SECURITY
_G = TechField.GAME
_E = TechField.EMBEDDED


# 정규 표기(name)는 자동으로 별칭에 들어가니 aliases 에 다시 쓰지 않는다
# 한글 표기를 하나 이상 넣는다. 별칭은 소문자로 비교한다
# cs_aliases 는 대소문자를 구분해 매칭한다 (CAN, ES 처럼 영어 단어와 겹치는 짧은 토큰용)
SKILL_CATALOG: tuple[SkillSeed, ...] = (
    SkillSeed("Java", "language", (_B, _M), ("자바",)),
    SkillSeed("Kotlin", "language", (_B, _M), ("코틀린",)),
    SkillSeed("Spring Boot", "framework", (_B,), ("스프링부트", "스프링 부트", "springboot")),
    SkillSeed("Spring Framework", "framework", (_B,), ("스프링", "spring", "스프링 프레임워크")),
    SkillSeed("JPA", "library", (_B,), ("hibernate", "하이버네이트", "spring data jpa")),
    SkillSeed("Node.js", "runtime", (_B,), ("nodejs", "노드js", "노드제이에스")),
    SkillSeed("NestJS", "framework", (_B,), ("nest.js", "네스트js")),
    SkillSeed("Express", "framework", (_B,), ("express.js", "expressjs", "익스프레스")),
    SkillSeed("Django", "framework", (_B, _D), ("장고",)),
    SkillSeed("FastAPI", "framework", (_B, _D), ("fast api", "패스트api")),
    SkillSeed("Flask", "framework", (_B,), ("플라스크",)),
    SkillSeed("Go", "language", (_B, _O), ("golang", "고랭", "go언어"), is_ambiguous=True),
    SkillSeed(
        "Elasticsearch",
        "database",
        (_D, _B),
        ("엘라스틱서치", "elastic search", "elk", "elk 스택"),
        cs_aliases=("ES",),
    ),
    SkillSeed("Ruby on Rails", "framework", (_B,), ("rails", "레일즈", "루비온레일즈")),
    SkillSeed("PHP", "language", (_B,), ("피에이치피",)),
    SkillSeed("Laravel", "framework", (_B,), ("라라벨",)),
    SkillSeed("ASP.NET", "framework", (_B,), ("asp.net core", "닷넷", ".net", "dotnet")),
    SkillSeed("MySQL", "database", (_B, _D), ("마이에스큐엘", "mariadb", "마리아db")),
    SkillSeed("Oracle", "database", (_B, _D), ("오라클", "oracle db", "oracle database")),
    SkillSeed("RabbitMQ", "infra", (_B,), ("래빗엠큐", "rabbit mq")),
    SkillSeed("Apache Tomcat", "infra", (_B,), ("tomcat", "톰캣")),
    SkillSeed("JUnit", "tool", (_B,), ("제이유닛", "junit5")),
    SkillSeed("QueryDSL", "library", (_B,), ("쿼리dsl", "query dsl")),
    SkillSeed("Supabase", "platform", (_B,), ("수파베이스",)),
    SkillSeed("Solidity", "language", (_B,), ("솔리디티",)),
    SkillSeed("PostgreSQL", "database", (_B, _D), ("포스트그레", "postgres", "psql")),
    SkillSeed("Redis", "database", (_B, _O), ("레디스",)),
    SkillSeed("MongoDB", "database", (_B, _D), ("몽고db", "몽고디비", "mongo")),
    SkillSeed("Kafka", "infra", (_B, _D), ("카프카", "apache kafka")),
    SkillSeed("GraphQL", "protocol", (_B, _F), ("그래프ql",)),
    SkillSeed("gRPC", "protocol", (_B,), ("grpc",)),
    SkillSeed("REST API", "protocol", (_B, _F), ("restful", "restful api", "레스트api")),
    SkillSeed("JavaScript", "language", (_F, _B), ("자바스크립트", "js", "es6", "ecmascript")),
    SkillSeed("TypeScript", "language", (_F, _B), ("타입스크립트", "ts")),
    SkillSeed("React", "library", (_F,), ("리액트", "react.js", "reactjs")),
    SkillSeed("Next.js", "framework", (_F,), ("nextjs", "넥스트js", "넥스트제이에스")),
    SkillSeed("Vue.js", "framework", (_F,), ("vuejs", "vue", "뷰js", "뷰제이에스")),
    SkillSeed("Nuxt.js", "framework", (_F,), ("nuxtjs", "nuxt", "눅스트")),
    SkillSeed("Angular", "framework", (_F,), ("앵귤러", "angularjs")),
    SkillSeed("Svelte", "framework", (_F,), ("스벨트", "sveltekit")),
    SkillSeed("HTML", "markup", (_F,), ("html5", "에이치티엠엘")),
    SkillSeed("CSS", "style", (_F,), ("css3", "css 3", "씨에스에스")),
    SkillSeed("React Query", "library", (_F,), ("리액트쿼리", "tanstack query")),
    SkillSeed("Sass", "style", (_F,), ("scss", "사스")),
    SkillSeed("Tailwind CSS", "style", (_F,), ("tailwind", "tailwindcss", "테일윈드")),
    SkillSeed("Redux", "library", (_F,), ("리덕스", "redux toolkit")),
    SkillSeed("Zustand", "library", (_F,), ("주스탠드",)),
    SkillSeed("Webpack", "tool", (_F,), ("웹팩",)),
    SkillSeed("Vite", "tool", (_F,), ("비테",)),
    SkillSeed("jQuery", "library", (_F,), ("제이쿼리",)),
    SkillSeed("Storybook", "tool", (_F,), ("스토리북",)),
    SkillSeed("Swift", "language", (_M,), ("스위프트",)),
    SkillSeed("SwiftUI", "framework", (_M,), ("스위프트ui",)),
    SkillSeed("Objective-C", "language", (_M,), ("objective c", "objc", "오브젝티브c")),
    SkillSeed("Android", "platform", (_M,), ("안드로이드", "android sdk")),
    SkillSeed("iOS", "platform", (_M,), ("아이오에스",)),
    SkillSeed("Jetpack Compose", "framework", (_M,), ("컴포즈",)),
    SkillSeed("React Native", "framework", (_M, _F), ("리액트네이티브", "rn")),
    SkillSeed("Flutter", "framework", (_M,), ("플러터",)),
    SkillSeed("Dart", "language", (_M,), ("다트",)),
    SkillSeed("Firebase", "platform", (_M, _B), ("파이어베이스",)),
    SkillSeed("Coroutines", "library", (_M, _B), ("코루틴", "kotlin coroutines")),
    SkillSeed("RxSwift", "library", (_M,), ("rx swift",)),
    SkillSeed("Retrofit", "library", (_M,), ("레트로핏",)),
    SkillSeed("Xcode", "tool", (_M,), ("엑스코드",)),
    SkillSeed("Python", "language", (_D, _B), ("파이썬",)),
    SkillSeed("R", "language", (_D,), ("r언어", "알언어"), is_ambiguous=True, cs_aliases=("R",)),
    SkillSeed("SQL", "language", (_D, _B), ("에스큐엘",)),
    SkillSeed("PyTorch", "framework", (_D,), ("파이토치", "torch")),
    SkillSeed("TensorFlow", "framework", (_D,), ("텐서플로", "텐서플로우", "tensorflow2")),
    SkillSeed("scikit-learn", "library", (_D,), ("sklearn", "사이킷런", "scikit learn")),
    SkillSeed("Pandas", "library", (_D,), ("판다스",)),
    SkillSeed("NumPy", "library", (_D,), ("넘파이",)),
    SkillSeed("Spark", "infra", (_D,), ("아파치 스파크", "apache spark", "pyspark")),
    SkillSeed("Hadoop", "infra", (_D,), ("하둡", "hdfs")),
    SkillSeed("Airflow", "tool", (_D, _O), ("에어플로우", "apache airflow")),
    SkillSeed("LangChain", "framework", (_D,), ("랭체인", "lang chain")),
    SkillSeed("Hugging Face", "platform", (_D,), ("허깅페이스", "huggingface", "transformers")),
    SkillSeed("OpenCV", "library", (_D, _E), ("오픈시브이", "open cv")),
    SkillSeed("MLflow", "tool", (_D,), ("ml flow", "엠엘플로우")),
    SkillSeed(
        "RAG",
        "domain",
        (_D,),
        ("검색증강생성", "retrieval augmented generation", "retrieval-augmented generation"),
        is_ambiguous=True,
        cs_aliases=("RAG",),
    ),
    SkillSeed(
        "Vector DB",
        "database",
        (_D, _B),
        ("벡터 db", "벡터db", "vectordb", "vector database", "벡터 데이터베이스", "벡터 검색"),
    ),
    SkillSeed("pgvector", "database", (_D, _B), ("pg vector", "피지벡터")),
    SkillSeed("Pinecone", "database", (_D,), ("파인콘",)),
    SkillSeed("Qdrant", "database", (_D,), ("큐드란트",)),
    SkillSeed("Milvus", "database", (_D,), ("밀버스",)),
    SkillSeed("Weaviate", "database", (_D,), ("위비에이트",)),
    SkillSeed("Chroma", "database", (_D,), ("chromadb", "크로마")),
    SkillSeed("FAISS", "library", (_D,), ("파이스",)),
    SkillSeed("LlamaIndex", "framework", (_D,), ("라마인덱스", "llama index", "gpt index")),
    SkillSeed("OpenAI API", "platform", (_D,), ("openai", "오픈에이아이", "gpt api", "chatgpt api")),
    SkillSeed(
        "Embedding",
        "domain",
        (_D,),
        ("임베딩", "embeddings", "벡터 임베딩", "text embedding"),
    ),
    SkillSeed(
        "AI/ML",
        "domain",
        (_D,),
        ("ai/인공지능", "인공지능", "머신러닝", "machine learning", "딥러닝", "deep learning"),
    ),
    SkillSeed("Tableau", "tool", (_D,), ("태블로",)),
    SkillSeed("dbt", "tool", (_D,), ("data build tool",)),
    SkillSeed("Docker", "infra", (_O, _B), ("도커",)),
    SkillSeed("Kubernetes", "infra", (_O,), ("쿠버네티스", "k8s", "쿠버")),
    SkillSeed("AWS", "cloud", (_O, _B), ("아마존 웹서비스", "amazon web services")),
    SkillSeed("GCP", "cloud", (_O,), ("google cloud", "구글 클라우드")),
    SkillSeed("Azure", "cloud", (_O,), ("애저", "microsoft azure")),
    SkillSeed("Terraform", "tool", (_O,), ("테라폼",)),
    SkillSeed("VMware", "infra", (_O,), ("브이엠웨어", "vm ware", "vsphere")),
    SkillSeed("Jenkins", "tool", (_O,), ("젠킨스",)),
    SkillSeed("GitHub Actions", "tool", (_O,), ("깃허브 액션", "github action")),
    SkillSeed("GitLab CI", "tool", (_O,), ("gitlab ci/cd", "깃랩 ci")),
    SkillSeed("ArgoCD", "tool", (_O,), ("argo cd", "아르고cd")),
    SkillSeed("Ansible", "tool", (_O,), ("앤서블",)),
    SkillSeed("Linux", "os", (_O, _E), ("리눅스", "ubuntu", "우분투", "centos")),
    SkillSeed("Nginx", "infra", (_O, _B), ("엔진엑스",)),
    SkillSeed("Prometheus", "tool", (_O,), ("프로메테우스",)),
    SkillSeed("Grafana", "tool", (_O,), ("그라파나",)),
    SkillSeed("Helm", "tool", (_O,), ("헬름",)),
    SkillSeed("모의해킹", "domain", (_S,), ("모의 해킹", "penetration test", "pentest")),
    SkillSeed("취약점 진단", "domain", (_S,), ("취약점진단", "vulnerability assessment")),
    SkillSeed("침해대응", "domain", (_S,), ("침해 대응", "incident response", "포렌식")),
    SkillSeed("ISMS", "compliance", (_S,), ("isms-p", "정보보호 인증")),
    SkillSeed("OWASP", "domain", (_S,), ("owasp top 10",)),
    SkillSeed("Burp Suite", "tool", (_S,), ("버프스위트", "burpsuite")),
    SkillSeed("Wireshark", "tool", (_S,), ("와이어샤크",)),
    SkillSeed("Metasploit", "tool", (_S,), ("메타스플로잇",)),
    SkillSeed("Nmap", "tool", (_S,), ("엔맵",)),
    SkillSeed("Splunk", "tool", (_S,), ("스플렁크",)),
    SkillSeed("SIEM", "tool", (_S,), ("보안관제", "보안 관제")),
    SkillSeed("Snort", "tool", (_S,), ("스노트", "ids/ips")),
    SkillSeed("Ghidra", "tool", (_S,), ("기드라",)),
    SkillSeed("IDA Pro", "tool", (_S,), ("ida", "아이다 프로")),
    SkillSeed("Kali Linux", "os", (_S,), ("칼리리눅스", "칼리 리눅스")),
    SkillSeed("리버싱", "domain", (_S,), ("reverse engineering", "역공학")),
    SkillSeed("Unity", "engine", (_G,), ("유니티", "unity3d")),
    SkillSeed("Unreal Engine", "engine", (_G,), ("언리얼", "unreal", "ue5", "ue4")),
    SkillSeed("C++", "language", (_G, _E), ("cpp", "씨쁠쁠", "시플플")),
    SkillSeed("C#", "language", (_G, _B), ("csharp", "씨샵", "c sharp")),
    SkillSeed("Lua", "language", (_G,), ("루아",)),
    SkillSeed("Godot", "engine", (_G,), ("고도 엔진",)),
    SkillSeed("Cocos2d", "engine", (_G,), ("코코스2d", "cocos")),
    SkillSeed("DirectX", "graphics", (_G,), ("다이렉트x", "direct x")),
    SkillSeed("OpenGL", "graphics", (_G, _E), ("오픈지엘",)),
    SkillSeed("Vulkan", "graphics", (_G,), ("불칸",)),
    SkillSeed("Shader", "graphics", (_G,), ("셰이더", "쉐이더", "hlsl", "glsl")),
    SkillSeed("Photon", "library", (_G,), ("포톤", "photon engine")),
    SkillSeed("Blender", "tool", (_G,), ("블렌더",)),
    SkillSeed("게임서버", "domain", (_G, _B), ("게임 서버", "game server")),
    SkillSeed("C", "language", (_E, _G), ("c언어", "씨언어"), is_ambiguous=True, cs_aliases=("C",)),
    SkillSeed("RTOS", "os", (_E,), ("실시간 운영체제",)),
    SkillSeed("FreeRTOS", "os", (_E,), ("free rtos", "프리rtos")),
    SkillSeed("Embedded Linux", "os", (_E,), ("임베디드 리눅스",)),
    SkillSeed("ARM", "hardware", (_E,), ("arm cortex", "cortex-m")),
    SkillSeed("STM32", "hardware", (_E,), ("stm 32",)),
    SkillSeed("Arduino", "hardware", (_E,), ("아두이노",)),
    SkillSeed("MCU", "hardware", (_E,), ("마이크로컨트롤러", "micro controller")),
    SkillSeed("Qt", "framework", (_E, _G), ("큐티 프레임워크",), cs_aliases=("Qt", "QT")),
    SkillSeed("Raspberry Pi", "hardware", (_E,), ("라즈베리파이", "라즈베리 파이")),
    SkillSeed(
        "CAN",
        "protocol",
        (_E,),
        ("can 통신", "canbus", "can bus"),
        is_ambiguous=True,
        cs_aliases=("CAN",),
    ),
    SkillSeed("AUTOSAR", "framework", (_E,), ("오토사",)),
    SkillSeed("Verilog", "hdl", (_E,), ("베릴로그", "systemverilog")),
    SkillSeed("VHDL", "hdl", (_E,), ("브이에이치디엘",)),
    SkillSeed("Yocto", "tool", (_E,), ("욕토", "yocto project")),
    SkillSeed("펌웨어", "domain", (_E,), ("firmware", "펌웨어 개발")),
    SkillSeed("I2C", "protocol", (_E,), ("i2c 통신", "spi")),
    SkillSeed(
        "Git",
        "tool",
        (_B, _F, _M, _D, _O),
        ("github", "깃허브", "gitlab", "깃랩"),
        is_common=True,
    ),
    SkillSeed("Jira", "tool", (_B, _F, _M), ("지라", "atlassian jira"), is_common=True),
    SkillSeed("Slack", "tool", (_B, _F, _M, _D, _O), ("슬랙",), is_common=True),
    SkillSeed("Notion", "tool", (_B, _F, _M, _D, _O), ("노션",), is_common=True),
    SkillSeed("Confluence", "tool", (_B, _F, _M), ("컨플루언스",), is_common=True),
    SkillSeed("Figma", "tool", (_F, _M), ("피그마",)),
)


def catalog_stats() -> dict[str, int]:
    counts: dict[str, int] = {f.code: 0 for f in FIELD_CATALOG}
    for skill in SKILL_CATALOG:
        for tech_field in skill.fields:
            counts[tech_field.value] += 1
    return counts
