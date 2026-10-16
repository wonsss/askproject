// 팀 공유(웹 콘솔 Cloudflare Tunnel + Access) 안내·입력. 설정 '팀 공유' 탭과 마법사 마지막 단계가 함께 쓴다.
// 단계마다 할 일 → 바로 가기 링크 → 그 단계에서 얻는 값의 입력 칸을 붙여 둔다.

import AppKit
import SwiftUI

/// Cloudflare 대시보드 경로 한곳. 대시보드 개편으로 경로가 바뀌면 여기만 고친다(2026-10 확인).
/// 계정 ID를 알면(터널 토큰에서 읽음) 그 계정 주소로, 모르면 `?to=/:account/…`(대시보드가 현재 계정으로 바꿔 줌)로 연다.
enum CloudflareDash {
    static let base = "https://dash.cloudflare.com"
    static let tunnels = "tunnels"
    static let accessApps = "one/access-controls/apps"  // Zero Trust → Access controls → Applications

    static func routes(tunnel: String) -> String { "tunnels/\(tunnel)/routes" }

    static func url(_ path: String, account: String?) -> URL {
        URL(string: account.map { "\(base)/\($0)/\(path)" } ?? "\(base)/?to=/:account/\(path)")!
    }
}

struct TeamShareForm: View {
    @ObservedObject var m: SettingsModel
    var isApp: Bool = Backend.isBundled

    private static let docs = "https://developers.cloudflare.com"
    // Cloudflare 문서가 쓰는 대시보드 바로 가기(`?to=/:account/…`). 메뉴 위치가 바뀌어도 해당 화면으로 연다

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("콘솔에는 슬랙 질문 원문과 답이 그대로 남아 있어요. 허용 목록은 운영을 맡은 몇 명으로 좁게 두세요.")
                .font(.caption).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true)

            step(1, "Cloudflare에 도메인 연결",
                 "가진 도메인을 Cloudflare에 추가하세요(없으면 Cloudflare에서 살 수 있어요). Zero Trust는 50명까지 무료예요.",
                 open: [("Cloudflare 대시보드", URL(string: CloudflareDash.base)!)],
                 guides: [("도메인 추가 방법", Self.docs + "/fundamentals/manage-domains/add-site/")]) { EmptyView() }

            step(2, "터널 만들기",
                 "Networking → Tunnels → Create a tunnel → Cloudflared. 이름(예: askproject)을 정하면 설치 명령이 나와요(OS는 아무거나). 명령 옆 복사 버튼으로 통째로 복사해 아래에 붙여넣고 '저장하고 터널 켜기'를 누르세요. 명령은 실행하지 않아요 — cloudflared는 앱에 들어 있어요. 대시보드의 Connection Status가 연결됨으로 바뀌면 Continue를 누를 수 있어요.",
                 open: [("Tunnels 열기", CloudflareDash.url(CloudflareDash.tunnels, account: nil))],
                 guides: [("터널 만드는 방법", Self.docs + "/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/")]) {
                TokenField(title: "터널 토큰", placeholder: "cloudflared service install eyJ… 또는 eyJ…", text: $m.shareToken,
                           problem: WizardModel.prefixProblem(SettingsModel.tunnelToken(m.shareToken), "eyJ", "터널 토큰"),
                           saved: m.shareTokenSaved)
                if isApp {
                    HStack {
                        Button(m.shareOn ? "터널 켜짐" : "저장하고 터널 켜기") { Task { await m.saveTokenAndStart() } }
                            .disabled(m.working || m.shareOn || (!m.shareTokenSaved && SettingsModel.tunnelToken(m.shareToken).isEmpty))
                        if m.shareOn {
                            Text("대시보드에서 연결됨이 보이면 Continue를 누르세요").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .controlSize(.small)
                } else {
                    Text("개발 앱: .env에 ASKPROJECT_CLOUDFLARE_TUNNEL_TOKEN을 넣고 터미널에서 uv run askproject tunnel")
                        .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
            }

            step(3, "공개 주소 정하기",
                 "2번 대시보드에서 Continue를 누르면 공개 주소(Published application) 화면이 나와요. 이미 만든 터널이면 터널 → Routes 탭 → Add route → Published application. 서브도메인(예: askproject)과 내 도메인을 고르고, Service URL은 http://127.0.0.1:8787로 두세요.",
                 open: [], guides: []) {
                HStack {
                    // 토큰을 넣었으면 그 터널의 Routes 화면을 바로 연다. 없으면 터널 목록(범용 바로 가기)
                    if let routes = m.routesURL {
                        DashButton(title: "Routes 열기", url: routes)
                    } else {
                        DashButton(title: "Tunnels 열기", url: CloudflareDash.url(CloudflareDash.tunnels, account: nil),
                                   help: "2번 터널 토큰을 넣으면 이 터널의 Routes 화면으로 바로 열려요")
                    }
                    // verbatim: 문자열 리터럴이면 SwiftUI가 URL을 링크로 바꿔 버튼 안에서 파랗게 보인다
                    Button { Self.copy("http://127.0.0.1:8787", m) } label: { Text(verbatim: "Service URL http://127.0.0.1:8787 복사") }
                }
                .controlSize(.small)
                LabeledField("공개 주소", placeholder: "askproject.example.com", text: $m.shareHost)
                problem(m.shareHostProblem)
            }

            step(4, "로그인 문 달기 (Access)",
                 "Zero Trust → Access controls → Applications → Create new application → Self-hosted. 3번과 같은 주소로 만들고, Allow 정책에 팀원 이메일을 넣으세요. 만든 앱의 Additional settings 탭 → AUD tag에서 Application Audience(AUD) 태그를 복사해요.",
                 open: [("Access 앱 열기", m.accessAppsURL)],
                 guides: [("Access 앱 만드는 방법", Self.docs + "/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/"),
                          ("AUD 태그 설명", Self.docs + "/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/")]) {
                LabeledField("Access AUD 태그", placeholder: "64자리 16진수", text: $m.shareAud)
                problem(m.shareAudProblem)
            }

            step(5, "팀 도메인 넣기",
                 "4번까지 마쳤다면 '공개 주소에서 찾기'를 누르세요. 공개 주소가 넘겨 주는 Access 로그인 주소에서 읽어 와요. 직접 넣으려면 Zero Trust → Overview의 Account details → Team domain이에요(팀 이름만 넣어도 돼요).",
                 open: [], guides: [("팀 도메인이란", Self.docs + "/cloudflare-one/faq/getting-started-faq/")]) {
                HStack(alignment: .bottom) {
                    LabeledField("Access 팀 도메인", placeholder: "myteam 또는 myteam.cloudflareaccess.com", text: $m.shareTeam)
                    Button(m.detectingTeam ? "찾는 중…" : "공개 주소에서 찾기") { Task { await m.detectTeam() } }
                        .disabled(m.detectingTeam || SettingsModel.host(m.shareHost).isEmpty || m.shareHostProblem != nil)
                }
                problem(m.shareTeamProblem)
                if let r = m.teamDetect, !r.ok { problem(r.detail) }
            }

            Text("저장한 뒤 '팀 공유 켜기'를 켜고 30초쯤 뒤 '연결 확인'을 누르세요. 팀원에게는 https://\(m.shareHost.isEmpty ? "<공개 주소>" : SettingsModel.host(m.shareHost)) 를 알려 주면 돼요.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
            if let c = m.shareCheck { NoteView(note: c) }
        }
    }

    /// open: 실제로 설정하러 가는 대시보드 화면(강조 버튼) · guides: 읽어 보는 설명 문서(조용한 텍스트 링크)
    private func step<Field: View>(_ n: Int, _ title: String, _ detail: String,
                                   open: [(String, URL)], guides: [(String, String)],
                                   @ViewBuilder field: () -> Field) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Text("\(n)").font(.caption.weight(.bold)).frame(width: 20, height: 20)
                        .background(Circle().fill(Color.accentColor.opacity(0.25)))
                    Text(title).font(.callout.weight(.semibold))
                }
                Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                if !open.isEmpty || !guides.isEmpty {
                    HStack(spacing: 12) {
                        ForEach(open, id: \.0) { l in DashButton(title: l.0, url: l.1) }
                        if !open.isEmpty && !guides.isEmpty { Spacer(minLength: 8) }
                        ForEach(guides, id: \.0) { l in GuideLink(title: l.0, url: URL(string: l.1)!) }
                    }
                }
                field()
            }
            .padding(6)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    @ViewBuilder
    private func problem(_ text: String?) -> some View {
        if let text { Text(text).font(.caption).foregroundStyle(.red) }
    }

    private static func copy(_ text: String, _ m: SettingsModel) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        m.note = CheckResult(ok: true, detail: "'\(text)'를 복사했어요. Cloudflare의 서비스 URL 칸에 붙여넣으세요.", step: nil)
    }
}

/// 팀 공유 켜기·연결 확인·저장. 설정 탭은 아래 고정 줄에, 마법사는 본문 아래에 둔다.
struct TeamShareControls: View {
    @ObservedObject var m: SettingsModel
    let isApp: Bool

    var body: some View {
        HStack {
            if isApp {
                Toggle("팀 공유 켜기", isOn: Binding(get: { m.shareOn }, set: { m.setShare($0) }))
                    .toggleStyle(.switch)
                    // 토큰만 있으면 켤 수 있다 — 대시보드는 터널이 연결돼야 공개 주소·Access 단계로 넘어간다
                    .disabled(m.working || (!m.shareOn && !m.shareTokenSaved))
                    .help(m.shareTokenSaved || m.shareOn ? "이 맥에서 터널을 켜고 꺼요" : "2번 터널 토큰을 저장하면 켤 수 있어요")
            } else {
                // 개발 앱은 SMAppService 에이전트가 없어 스위치를 둘 수 없다 — 잠긴 스위치 대신 할 일을 적는다
                Text("개발 앱: 터널은 터미널에서 uv run askproject tunnel")
                    .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            }
            Button(m.checkingShare ? "확인 중…" : "연결 확인") { Task { await m.checkShare() } }
                .disabled(m.checkingShare || !m.shareComplete)
            Spacer()
            if m.shareHasProblem {
                // 비활성 저장 버튼은 거의 보이지 않아, 왜 못 누르는지 옆에 적는다
                Text("빨간 안내를 고치면 저장할 수 있어요").font(.caption).foregroundStyle(.red)
            }
            Button("저장") { Task { await m.saveShare() } }
                .buttonStyle(.borderedProminent).disabled(m.working || m.shareHasProblem)
        }
    }
}

/// 실제로 설정하러 가는 Cloudflare 대시보드 화면 — 강조 버튼 + 바깥으로 여는 아이콘.
struct DashButton: View {
    let title: String
    let url: URL
    var help: String? = nil

    var body: some View {
        Button { NSWorkspace.shared.open(url) } label: { Label(title, systemImage: "arrow.up.right.square") }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
            .help(help ?? url.absoluteString)
    }
}

/// 읽어 보는 설명 문서 — 테두리 없는 작은 텍스트 링크 + 책 아이콘. 설정 버튼과 헷갈리지 않게 한다.
struct GuideLink: View {
    let title: String
    let url: URL

    var body: some View {
        Button { NSWorkspace.shared.open(url) } label: { Label(title, systemImage: "book") }
            .buttonStyle(.link)
            .font(.caption)
            .help("설명 문서: " + url.absoluteString)
    }
}
