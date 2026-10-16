// 데이터가 어디로 가는지 안내(마법사 첫 화면 카드 + 자세히 보기 창). 내용은 docs/privacy.md와 같게 유지한다.

import AppKit
import SwiftUI

enum Privacy {
    static let docURL = URL(string: "https://github.com/wonsss/askproject/blob/main/docs/privacy.md")!
    static let ackKey = "privacy.acknowledged"

    /// (아이콘, 제목, 설명) — 밖으로 나가는 통신 전부
    static let flows: [(String, String, String)] = [
        ("brain", "Anthropic Claude API",
         "질문, 관련 코드·문서 조각, 첨부한 스크린샷. 답을 만들려면 꼭 필요해요. 내 API 키와 Anthropic 약관(API 데이터는 모델 학습에 쓰지 않음)을 따라요."),
        ("bubble.left.and.bubble.right", "Slack", "답변·파일 게시, 첨부 이미지 받기, 지정한 채널 읽기"),
        ("arrow.down.circle", "GitHub · Notion", "코드·문서를 받기만 해요(읽기 전용 토큰)"),
        ("arrow.triangle.2.circlepath", "업데이트 확인", "새 버전이 있는지 묻는 요청만. 시스템 정보는 보내지 않아요"),
    ]

    static let never: [String] = [
        "AskProject 개발자 서버는 없어요. 텔레메트리·분석·오류 보고도 없어요.",
        "번들된 claude 실행 파일의 사용 지표·오류 보고도 꺼 두었어요.",
        "봇에는 읽기 전용 도구만 있어 코드 속 지시문으로도 데이터를 다른 곳에 보낼 수 없어요.",
    ]

    static let local: [String] = [
        "질문·답은 감사 로그로 이 맥에 영구 보관되고, 웹 콘솔은 이 맥(127.0.0.1)에서만 열려요.",
        "토큰·키는 macOS Keychain에 보관해요.",
    ]
}

/// 마법사 첫 화면의 짧은 안내
struct PrivacySummaryCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("데이터는 어디로 가나요", systemImage: "lock.shield").font(.callout.weight(.semibold))
            Text("질문과 관련 코드·문서 조각은 답을 만들려고 **Anthropic Claude API**로 보내져요(내 API 키 사용). 그 밖에는 내가 연결한 Slack·GitHub·Notion하고만 통신하고, **AskProject 개발자 서버나 텔레메트리는 없어요.** 질문·답은 이 맥에 보관돼요.")
                .font(.caption).fixedSize(horizontal: false, vertical: true)
            Button("자세히 보기") { WindowManager.shared.showPrivacy() }.buttonStyle(.link).font(.caption)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.accentColor.opacity(0.08)))
    }
}

/// 자세히 보기 창 (마법사·메뉴 막대 '더 보기'에서 연다)
struct PrivacyDetailView: View {
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                Text("데이터와 개인정보").font(.title2.weight(.semibold))
                Text("AskProject는 이 맥과 **내가 직접 연결한 서비스** 사이에서만 데이터를 주고받아요.")
                    .foregroundStyle(.secondary)
                section("밖으로 나가는 통신 (이게 전부예요)") {
                    ForEach(Privacy.flows, id: \.1) { icon, title, detail in
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: icon).frame(width: 20).foregroundStyle(Color.accentColor)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(title).font(.callout.weight(.medium))
                                Text(detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
                section("하지 않는 것") { bullets(Privacy.never, icon: "xmark.shield") }
                section("이 맥에 남는 것") { bullets(Privacy.local, icon: "internaldrive") }
                section("슬랙 사용자에게") {
                    Text("봇은 질문을 받을 때 \"질문과 관련 코드는 Claude API로 처리돼요. 개인정보는 넣지 마세요\"를 함께 보여 줘요. 채널 멤버에게도 미리 알려 주세요.")
                        .font(.caption).fixedSize(horizontal: false, vertical: true)
                }
                HStack {
                    Button("전체 문서 (통신 지점·코드 위치)") { NSWorkspace.shared.open(Privacy.docURL) }
                    Button("Anthropic 약관") { NSWorkspace.shared.open(URL(string: "https://www.anthropic.com/legal/commercial-terms")!) }
                }
            }
            .padding(24)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(width: 520, height: 560)
    }

    private func section<C: View>(_ title: String, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.headline)
            content()
        }
    }

    private func bullets(_ items: [String], icon: String) -> some View {
        ForEach(items, id: \.self) { t in
            Label { Text(t).font(.caption).fixedSize(horizontal: false, vertical: true) } icon: {
                Image(systemName: icon).foregroundStyle(.secondary)
            }
        }
    }
}
