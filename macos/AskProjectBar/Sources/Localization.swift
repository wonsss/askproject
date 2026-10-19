// 앱 화면 언어(한국어 · English). 한국어 원문이 곧 키다 — `L("한국어 문장")`은 영어일 때 사전에서 찾고, 없으면 원문을 쓴다.
// 빌드가 Xcode 없이 swiftc로 돌아 .lproj·String Catalog 대신 Swift 사전을 쓴다. 사전은 화면별 파일(`L10n+*.swift`)에 나눠 둔다.
// 백엔드 CLI가 돌려주는 메시지(오류 줄·점검 결과 등)는 번역하지 않는다.

import Foundation
import SwiftUI

enum AppLanguage: String, CaseIterable, Identifiable {
    case system, ko, en
    var id: String { rawValue }

    /// 고르는 목록에 보이는 이름. 언어 이름은 각 언어로 쓴다.
    var label: String {
        switch self {
        case .system: return L("시스템 설정 따르기")
        case .ko: return "한국어"
        case .en: return "English"
        }
    }
}

@MainActor
final class Localizer: ObservableObject {
    static let shared = Localizer()
    nonisolated static let key = "app.language"

    @Published var choice: AppLanguage {
        didSet {
            UserDefaults.standard.set(choice.rawValue, forKey: Self.key)
            Self.resolved = Self.resolve(choice)
        }
    }

    /// 실제로 쓰는 언어(ko | en). `L()`이 메인 스레드 밖에서도 읽으므로 정적 값으로 둔다.
    nonisolated(unsafe) static var resolved: AppLanguage = resolve(stored())

    private init() {
        choice = Self.stored()
    }

    nonisolated private static func stored() -> AppLanguage {
        AppLanguage(rawValue: UserDefaults.standard.string(forKey: key) ?? "") ?? .system
    }

    /// 시스템 따르기: 선호 언어 첫 번째가 한국어면 한국어, 아니면 영어.
    nonisolated private static func resolve(_ c: AppLanguage) -> AppLanguage {
        guard c == .system else { return c }
        let first = Locale.preferredLanguages.first ?? "ko"
        return first.hasPrefix("ko") ? .ko : .en
    }
}

/// 한국어 원문 → 현재 언어. 인자가 있으면 `%@`·`%d` 자리에 넣는다(키에도 같은 자리 표시를 쓴다).
func L(_ ko: String, _ args: CVarArg...) -> String {
    let text = Localizer.resolved == .en ? (L10n.en[ko] ?? ko) : ko
    return args.isEmpty ? text : String(format: text, arguments: args)
}

enum L10n {
    /// 영어 사전. 화면별 사전을 합친다(키가 겹치면 뒤의 것을 쓴다).
    static let en: [String: String] = [common, app, model, panel, settings, wizard, share, restore, learn, privacy]
        .reduce(into: [:]) { $0.merge($1) { _, new in new } }

    static let common: [String: String] = [
        "시스템 설정 따르기": "Follow system",
        "언어": "Language",
    ]
}

/// 언어를 바꾸면 화면 전체를 다시 그린다(하위 뷰가 `L()` 결과를 캐시하지 않게 id를 바꾼다).
struct LocalizedRoot<Content: View>: View {
    @ObservedObject private var lang = Localizer.shared
    let content: Content

    init(@ViewBuilder content: () -> Content) { self.content = content() }

    var body: some View {
        content
            .id(lang.choice)
            .environment(\.locale, Locale(identifier: Localizer.resolved.rawValue))
    }
}
