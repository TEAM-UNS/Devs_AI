# 분야와 스킬 사전 시드 데이터

from dataclasses import dataclass, field
from functools import cache

from app.domains.crawler.enums import TechField


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


@cache
def field_catalog() -> tuple[FieldSeed, ...]:
    return (
        FieldSeed(TechField.BACKEND, "백엔드", 10),
        FieldSeed(TechField.FRONTEND, "프론트엔드", 20),
        FieldSeed(TechField.MOBILE, "모바일", 30),
        FieldSeed(TechField.DATA_AI, "데이터/AI", 40),
        FieldSeed(TechField.DEVOPS, "DevOps/인프라", 50),
        FieldSeed(TechField.SECURITY, "보안", 60),
        FieldSeed(TechField.GAME, "게임", 70),
        FieldSeed(TechField.EMBEDDED, "임베디드", 80),
    )


# 정규 표기(name)는 자동으로 별칭에 들어가니 aliases 에 다시 쓰지 않는다
# 한글 표기를 하나 이상 넣는다. 별칭은 소문자로 비교한다
# cs_aliases 는 대소문자를 구분해 매칭한다 (CAN, ES 처럼 영어 단어와 겹치는 짧은 토큰용)
@cache
def skill_catalog() -> tuple[SkillSeed, ...]:
    b, f, m, d = TechField.BACKEND, TechField.FRONTEND, TechField.MOBILE, TechField.DATA_AI
    o, s, g, e = TechField.DEVOPS, TechField.SECURITY, TechField.GAME, TechField.EMBEDDED
    return (
        SkillSeed("Java", "language", (b, m), ("자바",)),
        SkillSeed("Kotlin", "language", (b, m), ("코틀린",)),
        SkillSeed("Spring Boot", "framework", (b,), ("스프링부트", "스프링 부트", "springboot")),
        SkillSeed("Spring Framework", "framework", (b,), ("스프링", "spring", "스프링 프레임워크")),
        SkillSeed("JPA", "library", (b,), ("hibernate", "하이버네이트", "spring data jpa")),
        SkillSeed("Node.js", "runtime", (b,), ("nodejs", "노드js", "노드제이에스")),
        SkillSeed("NestJS", "framework", (b,), ("nest.js", "네스트js")),
        SkillSeed("Express", "framework", (b,), ("express.js", "expressjs", "익스프레스")),
        SkillSeed("Django", "framework", (b, d), ("장고",)),
        SkillSeed("FastAPI", "framework", (b, d), ("fast api", "패스트api")),
        SkillSeed("Flask", "framework", (b,), ("플라스크",)),
        SkillSeed("Go", "language", (b, o), ("golang", "고랭", "go언어"), is_ambiguous=True),
        SkillSeed(
            "Elasticsearch",
            "database",
            (d, b),
            ("엘라스틱서치", "elastic search", "elk", "elk 스택"),
            cs_aliases=("ES",),
        ),
        SkillSeed("Ruby on Rails", "framework", (b,), ("rails", "레일즈", "루비온레일즈")),
        SkillSeed("PHP", "language", (b,), ("피에이치피",)),
        SkillSeed("Laravel", "framework", (b,), ("라라벨",)),
        SkillSeed("ASP.NET", "framework", (b,), ("asp.net core", "닷넷", ".net", "dotnet")),
        SkillSeed("MySQL", "database", (b, d), ("마이에스큐엘", "mariadb", "마리아db")),
        SkillSeed("Oracle", "database", (b, d), ("오라클", "oracle db", "oracle database")),
        SkillSeed("RabbitMQ", "infra", (b,), ("래빗엠큐", "rabbit mq")),
        SkillSeed("Apache Tomcat", "infra", (b,), ("tomcat", "톰캣")),
        SkillSeed("JUnit", "tool", (b,), ("제이유닛", "junit5")),
        SkillSeed("QueryDSL", "library", (b,), ("쿼리dsl", "query dsl")),
        SkillSeed("Supabase", "platform", (b,), ("수파베이스",)),
        SkillSeed("Solidity", "language", (b,), ("솔리디티",)),
        SkillSeed("PostgreSQL", "database", (b, d), ("포스트그레", "postgres", "psql")),
        SkillSeed("Redis", "database", (b, o), ("레디스",)),
        SkillSeed("MongoDB", "database", (b, d), ("몽고db", "몽고디비", "mongo")),
        SkillSeed("Kafka", "infra", (b, d), ("카프카", "apache kafka")),
        SkillSeed("GraphQL", "protocol", (b, f), ("그래프ql",)),
        SkillSeed("gRPC", "protocol", (b,), ("grpc",)),
        SkillSeed("REST API", "protocol", (b, f), ("restful", "restful api", "레스트api")),
        SkillSeed("JavaScript", "language", (f, b), ("자바스크립트", "js", "es6", "ecmascript")),
        SkillSeed("TypeScript", "language", (f, b), ("타입스크립트", "ts")),
        SkillSeed("React", "library", (f,), ("리액트", "react.js", "reactjs")),
        SkillSeed("Next.js", "framework", (f,), ("nextjs", "넥스트js", "넥스트제이에스")),
        SkillSeed("Vue.js", "framework", (f,), ("vuejs", "vue", "뷰js", "뷰제이에스")),
        SkillSeed("Nuxt.js", "framework", (f,), ("nuxtjs", "nuxt", "눅스트")),
        SkillSeed("Angular", "framework", (f,), ("앵귤러", "angularjs")),
        SkillSeed("Svelte", "framework", (f,), ("스벨트", "sveltekit")),
        SkillSeed("HTML", "markup", (f,), ("html5", "에이치티엠엘")),
        SkillSeed("CSS", "style", (f,), ("css3", "css 3", "씨에스에스")),
        SkillSeed("React Query", "library", (f,), ("리액트쿼리", "tanstack query")),
        SkillSeed("Sass", "style", (f,), ("scss", "사스")),
        SkillSeed("Tailwind CSS", "style", (f,), ("tailwind", "tailwindcss", "테일윈드")),
        SkillSeed("Redux", "library", (f,), ("리덕스", "redux toolkit")),
        SkillSeed("Zustand", "library", (f,), ("주스탠드",)),
        SkillSeed("Webpack", "tool", (f,), ("웹팩",)),
        SkillSeed("Vite", "tool", (f,), ("비테",)),
        SkillSeed("jQuery", "library", (f,), ("제이쿼리",)),
        SkillSeed("Storybook", "tool", (f,), ("스토리북",)),
        SkillSeed("Swift", "language", (m,), ("스위프트",)),
        SkillSeed("SwiftUI", "framework", (m,), ("스위프트ui",)),
        SkillSeed("Objective-C", "language", (m,), ("objective c", "objc", "오브젝티브c")),
        SkillSeed("Android", "platform", (m,), ("안드로이드", "android sdk")),
        SkillSeed("iOS", "platform", (m,), ("아이오에스",)),
        SkillSeed("Jetpack Compose", "framework", (m,), ("컴포즈",)),
        SkillSeed("React Native", "framework", (m, f), ("리액트네이티브", "rn")),
        SkillSeed("Flutter", "framework", (m,), ("플러터",)),
        SkillSeed("Dart", "language", (m,), ("다트",)),
        SkillSeed("Firebase", "platform", (m, b), ("파이어베이스",)),
        SkillSeed("Coroutines", "library", (m, b), ("코루틴", "kotlin coroutines")),
        SkillSeed("RxSwift", "library", (m,), ("rx swift",)),
        SkillSeed("Retrofit", "library", (m,), ("레트로핏",)),
        SkillSeed("Xcode", "tool", (m,), ("엑스코드",)),
        SkillSeed("Python", "language", (d, b), ("파이썬",)),
        SkillSeed("R", "language", (d,), ("r언어", "알언어"), is_ambiguous=True, cs_aliases=("R",)),
        SkillSeed("SQL", "language", (d, b), ("에스큐엘",)),
        SkillSeed("PyTorch", "framework", (d,), ("파이토치", "torch")),
        SkillSeed("TensorFlow", "framework", (d,), ("텐서플로", "텐서플로우", "tensorflow2")),
        SkillSeed("scikit-learn", "library", (d,), ("sklearn", "사이킷런", "scikit learn")),
        SkillSeed("Pandas", "library", (d,), ("판다스",)),
        SkillSeed("NumPy", "library", (d,), ("넘파이",)),
        SkillSeed("Spark", "infra", (d,), ("아파치 스파크", "apache spark", "pyspark")),
        SkillSeed("Hadoop", "infra", (d,), ("하둡", "hdfs")),
        SkillSeed("Airflow", "tool", (d, o), ("에어플로우", "apache airflow")),
        SkillSeed("LangChain", "framework", (d,), ("랭체인", "lang chain")),
        SkillSeed("Hugging Face", "platform", (d,), ("허깅페이스", "huggingface", "transformers")),
        SkillSeed("OpenCV", "library", (d, e), ("오픈시브이", "open cv")),
        SkillSeed("MLflow", "tool", (d,), ("ml flow", "엠엘플로우")),
        SkillSeed(
            "RAG",
            "domain",
            (d,),
            ("검색증강생성", "retrieval augmented generation", "retrieval-augmented generation"),
            is_ambiguous=True,
            cs_aliases=("RAG",),
        ),
        SkillSeed(
            "Vector DB",
            "database",
            (d, b),
            ("벡터 db", "벡터db", "vectordb", "vector database", "벡터 데이터베이스", "벡터 검색"),
        ),
        SkillSeed("pgvector", "database", (d, b), ("pg vector", "피지벡터")),
        SkillSeed("Pinecone", "database", (d,), ("파인콘",)),
        SkillSeed("Qdrant", "database", (d,), ("큐드란트",)),
        SkillSeed("Milvus", "database", (d,), ("밀버스",)),
        SkillSeed("Weaviate", "database", (d,), ("위비에이트",)),
        SkillSeed("Chroma", "database", (d,), ("chromadb", "크로마")),
        SkillSeed("FAISS", "library", (d,), ("파이스",)),
        SkillSeed("LlamaIndex", "framework", (d,), ("라마인덱스", "llama index", "gpt index")),
        SkillSeed(
            "OpenAI API", "platform", (d,), ("openai", "오픈에이아이", "gpt api", "chatgpt api")
        ),
        SkillSeed(
            "Embedding", "domain", (d,), ("임베딩", "embeddings", "벡터 임베딩", "text embedding")
        ),
        SkillSeed(
            "AI/ML",
            "domain",
            (d,),
            ("ai/인공지능", "인공지능", "머신러닝", "machine learning", "딥러닝", "deep learning"),
        ),
        SkillSeed("Tableau", "tool", (d,), ("태블로",)),
        SkillSeed("dbt", "tool", (d,), ("data build tool",)),
        SkillSeed("Docker", "infra", (o, b), ("도커",)),
        SkillSeed("Kubernetes", "infra", (o,), ("쿠버네티스", "k8s", "쿠버")),
        SkillSeed("AWS", "cloud", (o, b), ("아마존 웹서비스", "amazon web services")),
        SkillSeed("GCP", "cloud", (o,), ("google cloud", "구글 클라우드")),
        SkillSeed("Azure", "cloud", (o,), ("애저", "microsoft azure")),
        SkillSeed("Terraform", "tool", (o,), ("테라폼",)),
        SkillSeed("VMware", "infra", (o,), ("브이엠웨어", "vm ware", "vsphere")),
        SkillSeed("Jenkins", "tool", (o,), ("젠킨스",)),
        SkillSeed("GitHub Actions", "tool", (o,), ("깃허브 액션", "github action")),
        SkillSeed("GitLab CI", "tool", (o,), ("gitlab ci/cd", "깃랩 ci")),
        SkillSeed("ArgoCD", "tool", (o,), ("argo cd", "아르고cd")),
        SkillSeed("Ansible", "tool", (o,), ("앤서블",)),
        SkillSeed("Linux", "os", (o, e), ("리눅스", "ubuntu", "우분투", "centos")),
        SkillSeed("Nginx", "infra", (o, b), ("엔진엑스",)),
        SkillSeed("Prometheus", "tool", (o,), ("프로메테우스",)),
        SkillSeed("Grafana", "tool", (o,), ("그라파나",)),
        SkillSeed("Helm", "tool", (o,), ("헬름",)),
        SkillSeed("모의해킹", "domain", (s,), ("모의 해킹", "penetration test", "pentest")),
        SkillSeed("취약점 진단", "domain", (s,), ("취약점진단", "vulnerability assessment")),
        SkillSeed("침해대응", "domain", (s,), ("침해 대응", "incident response", "포렌식")),
        SkillSeed("ISMS", "compliance", (s,), ("isms-p", "정보보호 인증")),
        SkillSeed("OWASP", "domain", (s,), ("owasp top 10",)),
        SkillSeed("Burp Suite", "tool", (s,), ("버프스위트", "burpsuite")),
        SkillSeed("Wireshark", "tool", (s,), ("와이어샤크",)),
        SkillSeed("Metasploit", "tool", (s,), ("메타스플로잇",)),
        SkillSeed("Nmap", "tool", (s,), ("엔맵",)),
        SkillSeed("Splunk", "tool", (s,), ("스플렁크",)),
        SkillSeed("SIEM", "tool", (s,), ("보안관제", "보안 관제")),
        SkillSeed("Snort", "tool", (s,), ("스노트", "ids/ips")),
        SkillSeed("Ghidra", "tool", (s,), ("기드라",)),
        SkillSeed("IDA Pro", "tool", (s,), ("ida", "아이다 프로")),
        SkillSeed("Kali Linux", "os", (s,), ("칼리리눅스", "칼리 리눅스")),
        SkillSeed("리버싱", "domain", (s,), ("reverse engineering", "역공학")),
        SkillSeed("Unity", "engine", (g,), ("유니티", "unity3d")),
        SkillSeed("Unreal Engine", "engine", (g,), ("언리얼", "unreal", "ue5", "ue4")),
        SkillSeed("C++", "language", (g, e), ("cpp", "씨쁠쁠", "시플플")),
        SkillSeed("C#", "language", (g, b), ("csharp", "씨샵", "c sharp")),
        SkillSeed("Lua", "language", (g,), ("루아",)),
        SkillSeed("Godot", "engine", (g,), ("고도 엔진",)),
        SkillSeed("Cocos2d", "engine", (g,), ("코코스2d", "cocos")),
        SkillSeed("DirectX", "graphics", (g,), ("다이렉트x", "direct x")),
        SkillSeed("OpenGL", "graphics", (g, e), ("오픈지엘",)),
        SkillSeed("Vulkan", "graphics", (g,), ("불칸",)),
        SkillSeed("Shader", "graphics", (g,), ("셰이더", "쉐이더", "hlsl", "glsl")),
        SkillSeed("Photon", "library", (g,), ("포톤", "photon engine")),
        SkillSeed("Blender", "tool", (g,), ("블렌더",)),
        SkillSeed("게임서버", "domain", (g, b), ("게임 서버", "game server")),
        SkillSeed(
            "C", "language", (e, g), ("c언어", "씨언어"), is_ambiguous=True, cs_aliases=("C",)
        ),
        SkillSeed("RTOS", "os", (e,), ("실시간 운영체제",)),
        SkillSeed("FreeRTOS", "os", (e,), ("free rtos", "프리rtos")),
        SkillSeed("Embedded Linux", "os", (e,), ("임베디드 리눅스",)),
        SkillSeed("ARM", "hardware", (e,), ("arm cortex", "cortex-m")),
        SkillSeed("STM32", "hardware", (e,), ("stm 32",)),
        SkillSeed("Arduino", "hardware", (e,), ("아두이노",)),
        SkillSeed("MCU", "hardware", (e,), ("마이크로컨트롤러", "micro controller")),
        SkillSeed("Qt", "framework", (e, g), ("큐티 프레임워크",), cs_aliases=("Qt", "QT")),
        SkillSeed("Raspberry Pi", "hardware", (e,), ("라즈베리파이", "라즈베리 파이")),
        SkillSeed(
            "CAN",
            "protocol",
            (e,),
            ("can 통신", "canbus", "can bus"),
            is_ambiguous=True,
            cs_aliases=("CAN",),
        ),
        SkillSeed("AUTOSAR", "framework", (e,), ("오토사",)),
        SkillSeed("Verilog", "hdl", (e,), ("베릴로그", "systemverilog")),
        SkillSeed("VHDL", "hdl", (e,), ("브이에이치디엘",)),
        SkillSeed("Yocto", "tool", (e,), ("욕토", "yocto project")),
        SkillSeed("펌웨어", "domain", (e,), ("firmware", "펌웨어 개발")),
        SkillSeed("I2C", "protocol", (e,), ("i2c 통신", "spi")),
        SkillSeed(
            "Git", "tool", (b, f, m, d, o), ("github", "깃허브", "gitlab", "깃랩"), is_common=True
        ),
        SkillSeed("Jira", "tool", (b, f, m), ("지라", "atlassian jira"), is_common=True),
        SkillSeed("Slack", "tool", (b, f, m, d, o), ("슬랙",), is_common=True),
        SkillSeed("Notion", "tool", (b, f, m, d, o), ("노션",), is_common=True),
        SkillSeed("Confluence", "tool", (b, f, m), ("컨플루언스",), is_common=True),
        SkillSeed("Figma", "tool", (f, m), ("피그마",)),
    )


def catalog_stats() -> dict[str, int]:
    counts: dict[str, int] = {f.code: 0 for f in field_catalog()}
    for skill in skill_catalog():
        for tech_field in skill.fields:
            counts[tech_field.value] += 1
    return counts
