// 영어 사전 — 키는 화면의 한국어 원문(Localization.swift).

extension L10n {
    static let privacy: [String: String] = [
        "질문, 관련 코드·문서 조각, 첨부한 스크린샷. 답을 만들려면 꼭 필요해요. 내 API 키와 Anthropic 약관(API 데이터는 모델 학습에 쓰지 않음)을 따라요.": "Your question, relevant code and document snippets, and any screenshots you attach. This is required to produce an answer. It follows your API key and Anthropic's terms (API data is not used to train models).",
        "답변·파일 게시, 첨부 이미지 받기, 지정한 채널 읽기": "Posting answers and files, receiving attached images, reading the channels you specify",
        "코드·문서를 받기만 해요(읽기 전용 토큰)": "Only downloads code and documents (read-only token)",
        "업데이트 확인": "Check for updates",
        "새 버전이 있는지 묻는 요청만. 시스템 정보는 보내지 않아요": "Only a request asking whether a new version exists. No system information is sent",
        "팀 공유 (선택, Cloudflare Tunnel)": "Team sharing (optional, Cloudflare Tunnel)",
        "켰을 때만. 팀원이 웹 콘솔에서 보는 질문·답 화면이 Cloudflare를 거쳐 전달돼요. 기본은 꺼져 있어요.": "Only when turned on. The question and answer pages teammates view in the web console pass through Cloudflare. Off by default.",
        "AskProject 개발자 서버는 없어요. 텔레메트리·분석·오류 보고도 없어요.": "There is no AskProject developer server. No telemetry, analytics, or error reporting either.",
        "번들된 claude 실행 파일의 사용 지표·오류 보고도 꺼 두었어요.": "Usage metrics and error reporting in the bundled claude executable are turned off too.",
        "봇에는 읽기 전용 도구만 있어 코드 속 지시문으로도 데이터를 다른 곳에 보낼 수 없어요.": "The bot only has read-only tools, so even instructions hidden in code can't make it send data elsewhere.",
        "질문·답은 감사 로그로 이 맥에 영구 보관되고, 웹 콘솔은 이 맥(127.0.0.1)에서만 열려요(팀 공유를 켜면 Access 로그인을 거친 팀원도).": "Questions and answers are kept permanently on this Mac as an audit log, and the web console only opens on this Mac (127.0.0.1) — plus teammates who pass Access sign-in if team sharing is on.",
        "토큰·키는 macOS Keychain에 보관해요.": "Tokens and keys are stored in the macOS Keychain.",
        "데이터는 어디로 가나요": "Where does my data go?",
        "질문과 관련 코드·문서 조각은 답을 만들려고 **Anthropic Claude API**로 보내져요(내 API 키 사용). 그 밖에는 내가 연결한 Slack·GitHub·Notion하고만 통신하고, **AskProject 개발자 서버나 텔레메트리는 없어요.** 질문·답은 이 맥에 보관돼요.": "Your question and relevant code and document snippets are sent to the **Anthropic Claude API** to produce an answer (using your own API key). Otherwise it only talks to the Slack, GitHub, and Notion you connected, and **there is no AskProject developer server or telemetry.** Questions and answers are stored on this Mac.",
        "자세히 보기": "Learn more",
        "데이터와 개인정보": "Data & Privacy",
        "AskProject는 이 맥과 **내가 직접 연결한 서비스** 사이에서만 데이터를 주고받아요.": "AskProject only exchanges data between this Mac and **the services you connected yourself**.",
        "밖으로 나가는 통신 (이게 전부예요)": "Outgoing connections (this is all of them)",
        "하지 않는 것": "What it doesn't do",
        "이 맥에 남는 것": "What stays on this Mac",
        "슬랙 사용자에게": "For Slack users",
        "봇은 질문을 받을 때 \"질문과 관련 코드는 Claude API로 처리돼요. 개인정보는 넣지 마세요\"를 함께 보여 줘요. 채널 멤버에게도 미리 알려 주세요.": "When the bot receives a question, it also shows \"Your question and related code are processed by the Claude API. Don't include personal information.\" Please let channel members know in advance, too.",
        "전체 문서 (통신 지점·코드 위치)": "Full document (connection points · code locations)",
        "Anthropic 약관": "Anthropic terms",
    ]
}
