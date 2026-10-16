// 백엔드 JSON 출력의 모양. 새 필드는 옵셔널로 받아 이전 백엔드와도 디코딩되게 한다.

import Foundation
import SwiftUI

struct JobState: Decodable {
    let state: String          // running | waiting | off
    let pid: Int?
    let last_exit: String?
    let last_log: String?
}

struct Money: Decodable {
    let questions: Int
    let cost: Double
}

struct RecentQuestion: Decodable, Identifiable {
    let id: String
    let question: String
    let route: String
    let provider: String?
    let confidence: String?
    let ts: Double
    let cost: Double
    let flags: [String]
}

struct BackupInfo: Decodable {
    let name: String
    let age_hours: Double
    let offsite: Bool
}

struct ClaudeCheck: Decodable {
    let ok: Bool
    let problem: String?
}

struct PowerInfo: Decodable {
    let mode: String
    let autologin_user: String?
    let filevault_on: Bool
}

struct TokenExpiry: Decodable {
    let expires_at: String?
    let days_left: Double?
    let warning: Bool
}

struct OtherHost: Decodable {
    let host_name: String
}

struct HostConflict: Decodable {
    let others: [OtherHost]
}

struct AppStatus: Decodable {
    let version: String?
    let `protocol`: Int?
    let mode: String?           // app | dev
    let jobs: [String: JobState]
    let installed: Bool
    let configured: Bool?
    let console_url: String
    let console_key: String?
    let auth: String
    let provider: String?
    let claude_cli: Bool?
    let today: Money
    let week: Money
    let check: Int
    let recent: [RecentQuestion]
    let last_backup: BackupInfo?
    let claude: ClaudeCheck?
    let power: PowerInfo?
    let github_token: TokenExpiry?
    let allowed_channels: [String]?
    let host_conflict: HostConflict?
}

struct BackendPaths: Decodable {
    let mode: String
    let version: String
    let `protocol`: Int
    let config_dir: String
    let data_dir: String
    let log_dir: String
    let backup_dir: String
}

struct ConfigSettings: Decodable {
    let power_mode: String?
    let allowed_channels: String?
    let host_channel: String?
    let escalation_channel: String?
    let slack_source_channels: String?
    let notion_root_page_id: String?
    let model_large: String?
    let model_small: String?
    let console_public_host: String?
    let cf_access_team_domain: String?
    let cf_access_aud: String?
    let backup_copy_dir: String?
}

struct RepoEntry: Decodable, Identifiable, Hashable {
    var name: String
    var url: String
    var ref: String
    var description: String
    var id: String { name }
}

struct ConfigState: Decodable {
    let config_version: Int
    let mode: String
    let settings: ConfigSettings
    let secrets: [String: Bool]
    let repos: [RepoEntry]
    let tunnel: TunnelInfo?  // 저장된 터널 토큰의 계정·터널 ID(비밀 아님). 없으면 null
}

struct TunnelInfo: Decodable {
    let account_id: String
    let tunnel_id: String
    let routes_url: String
}

struct ConfigSetResult: Decodable {
    let ok: Bool
    let error: String?
}

struct CheckResult: Decodable {
    let ok: Bool
    let detail: String
    let step: String?
}

struct URLResult: Decodable {
    let url: String
}

struct TeamDomainResult: Decodable {
    let ok: Bool
    let detail: String
    let team_domain: String?
}

struct GitRepo: Decodable, Identifiable, Hashable {
    let full_name: String
    let name: String
    let url: String
    let default_branch: String
    let `private`: Bool
    let description: String
    var id: String { full_name }
}

struct RepoList: Decodable {
    let ok: Bool
    let detail: String
    let repos: [GitRepo]
}

struct SlackChannel: Decodable, Identifiable, Hashable {
    let id: String
    let name: String
}

struct ChannelList: Decodable {
    let ok: Bool
    let detail: String
    let channels: [SlackChannel]
}

struct DoctorItem: Decodable, Identifiable {
    let status: String   // ok | warn | fail
    let name: String
    let detail: String
    let required: Bool
    let step: String?
    var id: String { name }

    var symbol: String { status == "ok" ? "checkmark.circle.fill" : status == "warn" ? "exclamationmark.triangle.fill" : "xmark.octagon.fill" }
    var color: Color { status == "ok" ? .green : status == "warn" ? .orange : .red }
}

struct DiagnosticsResult: Decodable {
    let path: String
}

enum Health {
    case running, restarting, off, unknown

    var label: String {
        switch self {
        case .running: return "실행 중"
        case .restarting: return "재시작 대기"
        case .off: return "꺼짐"
        case .unknown: return "확인 중"
        }
    }

    var symbol: String {
        switch self {
        case .running: return "bubble.left.and.text.bubble.right.fill"
        case .restarting: return "exclamationmark.bubble.fill"
        case .off: return "bubble.left.and.bubble.right"
        case .unknown: return "ellipsis.bubble"
        }
    }

    var color: Color {
        switch self {
        case .running: return .green
        case .restarting: return .orange
        case .off, .unknown: return .secondary
        }
    }
}
