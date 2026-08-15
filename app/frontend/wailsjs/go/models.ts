export namespace brainbox {
	
	export class AgentDefinition {
	    name: string;
	    image: string;
	    description: string;
	    category: string;
	    spawn_mode: string;
	    capabilities: string[];
	    hardened: boolean;
	    persistent: boolean;
	    role_prompt?: string;
	    role_prompt_content?: string;
	    claude_model?: string;
	    claude_effort?: string;
	    codex_model?: string;
	    ollama_model?: string;
	
	    static createFrom(source: any = {}) {
	        return new AgentDefinition(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.image = source["image"];
	        this.description = source["description"];
	        this.category = source["category"];
	        this.spawn_mode = source["spawn_mode"];
	        this.capabilities = source["capabilities"];
	        this.hardened = source["hardened"];
	        this.persistent = source["persistent"];
	        this.role_prompt = source["role_prompt"];
	        this.role_prompt_content = source["role_prompt_content"];
	        this.claude_model = source["claude_model"];
	        this.claude_effort = source["claude_effort"];
	        this.codex_model = source["codex_model"];
	        this.ollama_model = source["ollama_model"];
	    }
	}
	export class AgentEventEntry {
	    seq: number;
	    id: string;
	    source: string;
	    type: string;
	    status: string;
	    parent_id: string;
	    ts: number;
	    envelope: Record<string, any>;
	
	    static createFrom(source: any = {}) {
	        return new AgentEventEntry(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.seq = source["seq"];
	        this.id = source["id"];
	        this.source = source["source"];
	        this.type = source["type"];
	        this.status = source["status"];
	        this.parent_id = source["parent_id"];
	        this.ts = source["ts"];
	        this.envelope = source["envelope"];
	    }
	}
	export class AgentStateItem {
	    id: string;
	    kind: string;
	    source: string;
	    type: string;
	    status: string;
	    title: string;
	    subtitle: string;
	    description: string;
	    workspace: string;
	    parent_id: string;
	    url: string;
	    start_at?: number;
	    end_at?: number;
	    tags: string[];
	    metadata: Record<string, any>;
	    actions: any[];
	    outcome: Record<string, any>;
	    created_at: number;
	    updated_at: number;
	
	    static createFrom(source: any = {}) {
	        return new AgentStateItem(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.kind = source["kind"];
	        this.source = source["source"];
	        this.type = source["type"];
	        this.status = source["status"];
	        this.title = source["title"];
	        this.subtitle = source["subtitle"];
	        this.description = source["description"];
	        this.workspace = source["workspace"];
	        this.parent_id = source["parent_id"];
	        this.url = source["url"];
	        this.start_at = source["start_at"];
	        this.end_at = source["end_at"];
	        this.tags = source["tags"];
	        this.metadata = source["metadata"];
	        this.actions = source["actions"];
	        this.outcome = source["outcome"];
	        this.created_at = source["created_at"];
	        this.updated_at = source["updated_at"];
	    }
	}
	export class ArtifactBucket {
	    key: string;
	    name: string;
	    label: string;
	    scope_prefix: string;
	
	    static createFrom(source: any = {}) {
	        return new ArtifactBucket(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.key = source["key"];
	        this.name = source["name"];
	        this.label = source["label"];
	        this.scope_prefix = source["scope_prefix"];
	    }
	}
	export class ArtifactFile {
	    name: string;
	    key: string;
	    size: number;
	    etag: string;
	    last_modified_ms: number;
	
	    static createFrom(source: any = {}) {
	        return new ArtifactFile(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.key = source["key"];
	        this.size = source["size"];
	        this.etag = source["etag"];
	        this.last_modified_ms = source["last_modified_ms"];
	    }
	}
	export class ArtifactFolder {
	    name: string;
	    prefix: string;
	
	    static createFrom(source: any = {}) {
	        return new ArtifactFolder(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.prefix = source["prefix"];
	    }
	}
	export class ArtifactListing {
	    bucket: string;
	    prefix: string;
	    truncated: boolean;
	    folders: ArtifactFolder[];
	    files: ArtifactFile[];
	
	    static createFrom(source: any = {}) {
	        return new ArtifactListing(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.bucket = source["bucket"];
	        this.prefix = source["prefix"];
	        this.truncated = source["truncated"];
	        this.folders = this.convertValues(source["folders"], ArtifactFolder);
	        this.files = this.convertValues(source["files"], ArtifactFile);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class ArtifactObjectHead {
	    bucket: string;
	    key: string;
	    size: number;
	    etag: string;
	    content_type: string;
	    last_modified_ms: number;
	    metadata: Record<string, string>;
	
	    static createFrom(source: any = {}) {
	        return new ArtifactObjectHead(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.bucket = source["bucket"];
	        this.key = source["key"];
	        this.size = source["size"];
	        this.etag = source["etag"];
	        this.content_type = source["content_type"];
	        this.last_modified_ms = source["last_modified_ms"];
	        this.metadata = source["metadata"];
	    }
	}
	export class ArtifactPresignedURL {
	    url: string;
	    expires_in: number;
	
	    static createFrom(source: any = {}) {
	        return new ArtifactPresignedURL(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.url = source["url"];
	        this.expires_in = source["expires_in"];
	    }
	}
	export class ArtifactsHealth {
	    ok: boolean;
	    reason?: string;
	    endpoint?: string;
	    buckets?: Record<string, string>;
	    profile_prefix?: string;
	    protected_bucket?: string;
	    protected_dirs?: string[];
	
	    static createFrom(source: any = {}) {
	        return new ArtifactsHealth(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.ok = source["ok"];
	        this.reason = source["reason"];
	        this.endpoint = source["endpoint"];
	        this.buckets = source["buckets"];
	        this.profile_prefix = source["profile_prefix"];
	        this.protected_bucket = source["protected_bucket"];
	        this.protected_dirs = source["protected_dirs"];
	    }
	}
	export class BrainProfileInfo {
	    profile: string;
	    vault: string;
	    provisioned: boolean;
	    bucket: string;
	    index_prefix: string;
	
	    static createFrom(source: any = {}) {
	        return new BrainProfileInfo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.vault = source["vault"];
	        this.provisioned = source["provisioned"];
	        this.bucket = source["bucket"];
	        this.index_prefix = source["index_prefix"];
	    }
	}
	export class BrainProfileInitResult {
	    profile: string;
	    vault: string;
	    provisioned: boolean;
	    live: boolean;
	    token_created: boolean;
	    bucket: string;
	    index_prefix: string;
	    threaded: boolean;
	
	    static createFrom(source: any = {}) {
	        return new BrainProfileInitResult(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.vault = source["vault"];
	        this.provisioned = source["provisioned"];
	        this.live = source["live"];
	        this.token_created = source["token_created"];
	        this.bucket = source["bucket"];
	        this.index_prefix = source["index_prefix"];
	        this.threaded = source["threaded"];
	    }
	}
	export class BrainVaultToken {
	    vault: string;
	    token: string;
	    is_default: boolean;
	
	    static createFrom(source: any = {}) {
	        return new BrainVaultToken(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.vault = source["vault"];
	        this.token = source["token"];
	        this.is_default = source["is_default"];
	    }
	}
	export class BrainProfileTokens {
	    profile: string;
	    session_url: string;
	    default_vault: string;
	    tokens: BrainVaultToken[];
	
	    static createFrom(source: any = {}) {
	        return new BrainProfileTokens(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.session_url = source["session_url"];
	        this.default_vault = source["default_vault"];
	        this.tokens = this.convertValues(source["tokens"], BrainVaultToken);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class BrainSkill {
	    name: string;
	    title: string;
	    sha: string;
	    updated_at: string;
	    tags: string[];
	
	    static createFrom(source: any = {}) {
	        return new BrainSkill(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.title = source["title"];
	        this.sha = source["sha"];
	        this.updated_at = source["updated_at"];
	        this.tags = source["tags"];
	    }
	}
	export class BrainSkillDetail {
	    name: string;
	    title: string;
	    body: string;
	    sha: string;
	    updated_at: string;
	
	    static createFrom(source: any = {}) {
	        return new BrainSkillDetail(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.title = source["title"];
	        this.body = source["body"];
	        this.sha = source["sha"];
	        this.updated_at = source["updated_at"];
	    }
	}
	export class BrainSkillWriteResult {
	    name: string;
	    title: string;
	    sha: string;
	    created: boolean;
	    replaced: number;
	    deleted: number;
	
	    static createFrom(source: any = {}) {
	        return new BrainSkillWriteResult(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.title = source["title"];
	        this.sha = source["sha"];
	        this.created = source["created"];
	        this.replaced = source["replaced"];
	        this.deleted = source["deleted"];
	    }
	}
	
	export class BundleMeta {
	    profile: string;
	    etag: string;
	    size: number;
	    last_modified_ms: number;
	    captured_at: string;
	    app_version: string;
	
	    static createFrom(source: any = {}) {
	        return new BundleMeta(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.etag = source["etag"];
	        this.size = source["size"];
	        this.last_modified_ms = source["last_modified_ms"];
	        this.captured_at = source["captured_at"];
	        this.app_version = source["app_version"];
	    }
	}
	export class BundlePutResult {
	    profile: string;
	    saved: boolean;
	    etag: string;
	    captured_at: string;
	    sources: string[];
	    size: number;
	
	    static createFrom(source: any = {}) {
	        return new BundlePutResult(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.saved = source["saved"];
	        this.etag = source["etag"];
	        this.captured_at = source["captured_at"];
	        this.sources = source["sources"];
	        this.size = source["size"];
	    }
	}
	export class ChannelParticipant {
	    name: string;
	    type: string;
	    session_name?: string;
	    ollama_model?: string;
	    system_prompt?: string;
	    joined_at: number;
	
	    static createFrom(source: any = {}) {
	        return new ChannelParticipant(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.type = source["type"];
	        this.session_name = source["session_name"];
	        this.ollama_model = source["ollama_model"];
	        this.system_prompt = source["system_prompt"];
	        this.joined_at = source["joined_at"];
	    }
	}
	export class Channel {
	    id: string;
	    name: string;
	    participants: ChannelParticipant[];
	    status: string;
	    created_at: number;
	    completed_at?: number;
	    completed_by?: string;
	    parent_task_id?: string;
	    workspace_profile?: string;
	
	    static createFrom(source: any = {}) {
	        return new Channel(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.name = source["name"];
	        this.participants = this.convertValues(source["participants"], ChannelParticipant);
	        this.status = source["status"];
	        this.created_at = source["created_at"];
	        this.completed_at = source["completed_at"];
	        this.completed_by = source["completed_by"];
	        this.parent_task_id = source["parent_task_id"];
	        this.workspace_profile = source["workspace_profile"];
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class ChannelMessage {
	    id: string;
	    channel_id: string;
	    from_participant: string;
	    content: string;
	    summary?: string;
	    addressed_to?: string;
	    type: string;
	    timestamp: number;
	
	    static createFrom(source: any = {}) {
	        return new ChannelMessage(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.channel_id = source["channel_id"];
	        this.from_participant = source["from_participant"];
	        this.content = source["content"];
	        this.summary = source["summary"];
	        this.addressed_to = source["addressed_to"];
	        this.type = source["type"];
	        this.timestamp = source["timestamp"];
	    }
	}
	
	export class ChannelParticipantRequest {
	    name: string;
	    type: string;
	    session_name?: string;
	    ollama_model?: string;
	    system_prompt?: string;
	
	    static createFrom(source: any = {}) {
	        return new ChannelParticipantRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.type = source["type"];
	        this.session_name = source["session_name"];
	        this.ollama_model = source["ollama_model"];
	        this.system_prompt = source["system_prompt"];
	    }
	}
	export class CompleteChannelRequest {
	    by: string;
	    reason?: string;
	
	    static createFrom(source: any = {}) {
	        return new CompleteChannelRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.by = source["by"];
	        this.reason = source["reason"];
	    }
	}
	export class ContainerMetrics {
	    name: string;
	    cpu_percent: number;
	    memory_bytes: number;
	    uptime: string;
	
	    static createFrom(source: any = {}) {
	        return new ContainerMetrics(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.cpu_percent = source["cpu_percent"];
	        this.memory_bytes = source["memory_bytes"];
	        this.uptime = source["uptime"];
	    }
	}
	export class CreateAgentRequest {
	    name: string;
	    image?: string;
	    description?: string;
	    category?: string;
	    spawn_mode?: string;
	    capabilities?: string[];
	    hardened?: boolean;
	    persistent?: boolean;
	    role_prompt_content?: string;
	    claude_model?: string;
	    claude_effort?: string;
	    codex_model?: string;
	    ollama_model?: string;
	
	    static createFrom(source: any = {}) {
	        return new CreateAgentRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.image = source["image"];
	        this.description = source["description"];
	        this.category = source["category"];
	        this.spawn_mode = source["spawn_mode"];
	        this.capabilities = source["capabilities"];
	        this.hardened = source["hardened"];
	        this.persistent = source["persistent"];
	        this.role_prompt_content = source["role_prompt_content"];
	        this.claude_model = source["claude_model"];
	        this.claude_effort = source["claude_effort"];
	        this.codex_model = source["codex_model"];
	        this.ollama_model = source["ollama_model"];
	    }
	}
	export class CreateChannelRequest {
	    name: string;
	    participants: ChannelParticipantRequest[];
	    parent_task_id?: string;
	    workspace_profile?: string;
	
	    static createFrom(source: any = {}) {
	        return new CreateChannelRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.participants = this.convertValues(source["participants"], ChannelParticipantRequest);
	        this.parent_task_id = source["parent_task_id"];
	        this.workspace_profile = source["workspace_profile"];
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class CreateSessionRequest {
	    name: string;
	    role?: string;
	    volume?: string;
	    volumes?: string[];
	    llm_provider?: string;
	    llm_model?: string;
	    ollama_host?: string;
	    codex_api_key?: string;
	    backend?: string;
	    vm_template?: string;
	    guest_os?: string;
	    workspace_profile?: string;
	    workspace_home?: string;
	    task?: string;
	    continue_from?: string;
	    ports?: Record<string, number>;
	    docker_host?: string;
	    runner?: string;
	    delivery?: string;
	    env?: Record<string, string>;
	
	    static createFrom(source: any = {}) {
	        return new CreateSessionRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.role = source["role"];
	        this.volume = source["volume"];
	        this.volumes = source["volumes"];
	        this.llm_provider = source["llm_provider"];
	        this.llm_model = source["llm_model"];
	        this.ollama_host = source["ollama_host"];
	        this.codex_api_key = source["codex_api_key"];
	        this.backend = source["backend"];
	        this.vm_template = source["vm_template"];
	        this.guest_os = source["guest_os"];
	        this.workspace_profile = source["workspace_profile"];
	        this.workspace_home = source["workspace_home"];
	        this.task = source["task"];
	        this.continue_from = source["continue_from"];
	        this.ports = source["ports"];
	        this.docker_host = source["docker_host"];
	        this.runner = source["runner"];
	        this.delivery = source["delivery"];
	        this.env = source["env"];
	    }
	}
	export class DispatchCandidate {
	    name: string;
	    version: string;
	    tags: string[];
	    online: boolean;
	    supports_backend: boolean;
	    tag_score?: number;
	
	    static createFrom(source: any = {}) {
	        return new DispatchCandidate(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.version = source["version"];
	        this.tags = source["tags"];
	        this.online = source["online"];
	        this.supports_backend = source["supports_backend"];
	        this.tag_score = source["tag_score"];
	    }
	}
	export class DispatchPreview {
	    selected_runner?: string;
	    in_process: boolean;
	    reason: string;
	    candidates: DispatchCandidate[];
	    error?: string;
	
	    static createFrom(source: any = {}) {
	        return new DispatchPreview(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.selected_runner = source["selected_runner"];
	        this.in_process = source["in_process"];
	        this.reason = source["reason"];
	        this.candidates = this.convertValues(source["candidates"], DispatchCandidate);
	        this.error = source["error"];
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class DispatchPreviewRequest {
	    backend?: string;
	    runner?: string;
	    tags?: string[];
	
	    static createFrom(source: any = {}) {
	        return new DispatchPreviewRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.backend = source["backend"];
	        this.runner = source["runner"];
	        this.tags = source["tags"];
	    }
	}
	export class GatewayProfilesInfo {
	    profiles: string[];
	    unlocked: boolean;
	
	    static createFrom(source: any = {}) {
	        return new GatewayProfilesInfo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profiles = source["profiles"];
	        this.unlocked = source["unlocked"];
	    }
	}
	export class GatewayServer {
	    name: string;
	    command: string;
	    enabled: boolean;
	
	    static createFrom(source: any = {}) {
	        return new GatewayServer(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.command = source["command"];
	        this.enabled = source["enabled"];
	    }
	}
	export class GatewayToken {
	    token: string;
	    profile: string;
	    scope: string[];
	    ceiling: string;
	    expiry: number;
	
	    static createFrom(source: any = {}) {
	        return new GatewayToken(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.token = source["token"];
	        this.profile = source["profile"];
	        this.scope = source["scope"];
	        this.ceiling = source["ceiling"];
	        this.expiry = source["expiry"];
	    }
	}
	export class GatewayTool {
	    name: string;
	    description: string;
	
	    static createFrom(source: any = {}) {
	        return new GatewayTool(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.description = source["description"];
	    }
	}
	export class GatewayToolsResult {
	    profile: string;
	    servers: string[];
	    tools: GatewayTool[];
	
	    static createFrom(source: any = {}) {
	        return new GatewayToolsResult(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.servers = source["servers"];
	        this.tools = this.convertValues(source["tools"], GatewayTool);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class HealthStatus {
	    status: string;
	    message: string;
	
	    static createFrom(source: any = {}) {
	        return new HealthStatus(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.status = source["status"];
	        this.message = source["message"];
	    }
	}
	export class IntegrationPlacement {
	    name: string;
	    node: string;
	    desired: string;
	    updated_at: number;
	
	    static createFrom(source: any = {}) {
	        return new IntegrationPlacement(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.node = source["node"];
	        this.desired = source["desired"];
	        this.updated_at = source["updated_at"];
	    }
	}
	export class IntegrationConsumer {
	    target: string;
	    server: string;
	    env: string;
	
	    static createFrom(source: any = {}) {
	        return new IntegrationConsumer(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.target = source["target"];
	        this.server = source["server"];
	        this.env = source["env"];
	    }
	}
	export class Integration {
	    name: string;
	    description: string;
	    capability: string;
	    consumers: IntegrationConsumer[];
	    placement?: IntegrationPlacement;
	    endpoint: string;
	
	    static createFrom(source: any = {}) {
	        return new Integration(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.description = source["description"];
	        this.capability = source["capability"];
	        this.consumers = this.convertValues(source["consumers"], IntegrationConsumer);
	        this.placement = this.convertValues(source["placement"], IntegrationPlacement);
	        this.endpoint = source["endpoint"];
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	
	export class IntegrationNode {
	    name: string;
	    host: string;
	
	    static createFrom(source: any = {}) {
	        return new IntegrationNode(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.host = source["host"];
	    }
	}
	
	export class IntegrationsList {
	    integrations: Integration[];
	    nodes: IntegrationNode[];
	
	    static createFrom(source: any = {}) {
	        return new IntegrationsList(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.integrations = this.convertValues(source["integrations"], Integration);
	        this.nodes = this.convertValues(source["nodes"], IntegrationNode);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class LiveLoop {
	    id: string;
	    template_name: string;
	    template_text: string;
	    template_hash: string;
	    mermaid: string;
	    parent_task_id: string;
	    status: string;
	    iteration: number;
	    envelope: Record<string, any>;
	    cost_history: number[];
	    cost_usd: number;
	    current_child_id?: string;
	    workspace_profile?: string;
	    created_at: number;
	    updated_at: number;
	    error?: string;
	    stop_reason?: string;
	
	    static createFrom(source: any = {}) {
	        return new LiveLoop(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.template_name = source["template_name"];
	        this.template_text = source["template_text"];
	        this.template_hash = source["template_hash"];
	        this.mermaid = source["mermaid"];
	        this.parent_task_id = source["parent_task_id"];
	        this.status = source["status"];
	        this.iteration = source["iteration"];
	        this.envelope = source["envelope"];
	        this.cost_history = source["cost_history"];
	        this.cost_usd = source["cost_usd"];
	        this.current_child_id = source["current_child_id"];
	        this.workspace_profile = source["workspace_profile"];
	        this.created_at = source["created_at"];
	        this.updated_at = source["updated_at"];
	        this.error = source["error"];
	        this.stop_reason = source["stop_reason"];
	    }
	}
	export class LiveLoopIteration {
	    loop_id: string;
	    iteration: number;
	    convergence_metric_value: number;
	    duration_ms: number;
	    cost_usd: number;
	    tokens: number;
	    model?: string;
	    state_at_end?: string;
	    timestamp: number;
	
	    static createFrom(source: any = {}) {
	        return new LiveLoopIteration(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.loop_id = source["loop_id"];
	        this.iteration = source["iteration"];
	        this.convergence_metric_value = source["convergence_metric_value"];
	        this.duration_ms = source["duration_ms"];
	        this.cost_usd = source["cost_usd"];
	        this.tokens = source["tokens"];
	        this.model = source["model"];
	        this.state_at_end = source["state_at_end"];
	        this.timestamp = source["timestamp"];
	    }
	}
	export class LiveLoopSummary {
	    id: string;
	    name: string;
	    status: string;
	    iteration: number;
	    max_iterations: number;
	    parent_task_id: string;
	    current_child_id?: string;
	    cost_history: number[];
	    cost_usd: number;
	    mermaid?: string;
	    stop_reason?: string;
	    error?: string;
	    workspace_profile?: string;
	    created_at: number;
	    updated_at: number;
	
	    static createFrom(source: any = {}) {
	        return new LiveLoopSummary(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.name = source["name"];
	        this.status = source["status"];
	        this.iteration = source["iteration"];
	        this.max_iterations = source["max_iterations"];
	        this.parent_task_id = source["parent_task_id"];
	        this.current_child_id = source["current_child_id"];
	        this.cost_history = source["cost_history"];
	        this.cost_usd = source["cost_usd"];
	        this.mermaid = source["mermaid"];
	        this.stop_reason = source["stop_reason"];
	        this.error = source["error"];
	        this.workspace_profile = source["workspace_profile"];
	        this.created_at = source["created_at"];
	        this.updated_at = source["updated_at"];
	    }
	}
	export class LoopAssistRequest {
	    mode: string;
	    prompt: string;
	    current_yaml?: string;
	    selection?: Record<string, any>;
	    save_as?: string;
	
	    static createFrom(source: any = {}) {
	        return new LoopAssistRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.mode = source["mode"];
	        this.prompt = source["prompt"];
	        this.current_yaml = source["current_yaml"];
	        this.selection = source["selection"];
	        this.save_as = source["save_as"];
	    }
	}
	export class LoopAssistResult {
	    yaml: string;
	    explanation: string;
	    model: string;
	    tokens: Record<string, number>;
	    cost_usd: number;
	    warnings: any[];
	    retries: number;
	    saved_to?: string;
	    save_error?: string;
	
	    static createFrom(source: any = {}) {
	        return new LoopAssistResult(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.yaml = source["yaml"];
	        this.explanation = source["explanation"];
	        this.model = source["model"];
	        this.tokens = source["tokens"];
	        this.cost_usd = source["cost_usd"];
	        this.warnings = source["warnings"];
	        this.retries = source["retries"];
	        this.saved_to = source["saved_to"];
	        this.save_error = source["save_error"];
	    }
	}
	export class LoopTemplate {
	    name: string;
	    origin: string;
	    hash: string;
	    markdown: string;
	
	    static createFrom(source: any = {}) {
	        return new LoopTemplate(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.origin = source["origin"];
	        this.hash = source["hash"];
	        this.markdown = source["markdown"];
	    }
	}
	export class LoopTemplateValidationEntry {
	    line?: number;
	    col?: number;
	    field?: string;
	    message: string;
	
	    static createFrom(source: any = {}) {
	        return new LoopTemplateValidationEntry(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.line = source["line"];
	        this.col = source["col"];
	        this.field = source["field"];
	        this.message = source["message"];
	    }
	}
	export class LoopTemplateValidation {
	    ok: boolean;
	    errors: LoopTemplateValidationEntry[];
	    warnings: LoopTemplateValidationEntry[];
	
	    static createFrom(source: any = {}) {
	        return new LoopTemplateValidation(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.ok = source["ok"];
	        this.errors = this.convertValues(source["errors"], LoopTemplateValidationEntry);
	        this.warnings = this.convertValues(source["warnings"], LoopTemplateValidationEntry);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	
	export class Message {
	    id: string;
	    sender: string;
	    recipient: string;
	    type: string;
	    payload: any;
	    timestamp: string;
	
	    static createFrom(source: any = {}) {
	        return new Message(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.sender = source["sender"];
	        this.recipient = source["recipient"];
	        this.type = source["type"];
	        this.payload = source["payload"];
	        this.timestamp = source["timestamp"];
	    }
	}
	export class MetricsSample {
	    ts: number;
	    agent_count: number;
	    total_cpu: number;
	    total_mem: number;
	
	    static createFrom(source: any = {}) {
	        return new MetricsSample(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.ts = source["ts"];
	        this.agent_count = source["agent_count"];
	        this.total_cpu = source["total_cpu"];
	        this.total_mem = source["total_mem"];
	    }
	}
	export class OllamaModel {
	    name: string;
	    size: number;
	    modified_at: string;
	    digest: string;
	
	    static createFrom(source: any = {}) {
	        return new OllamaModel(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.size = source["size"];
	        this.modified_at = source["modified_at"];
	        this.digest = source["digest"];
	    }
	}
	export class ToolZone {
	    name: string;
	    zone: string;
	
	    static createFrom(source: any = {}) {
	        return new ToolZone(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.zone = source["zone"];
	    }
	}
	export class ProviderZone {
	    name: string;
	    zone: string;
	    capabilities: string[];
	
	    static createFrom(source: any = {}) {
	        return new ProviderZone(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.zone = source["zone"];
	        this.capabilities = source["capabilities"];
	    }
	}
	export class OrchestrationZones {
	    profile: string;
	    providers: ProviderZone[];
	    tools: ToolZone[];
	
	    static createFrom(source: any = {}) {
	        return new OrchestrationZones(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.providers = this.convertValues(source["providers"], ProviderZone);
	        this.tools = this.convertValues(source["tools"], ToolZone);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class PairingTicket {
	    token: string;
	    expires_at: number;
	    api_url: string;
	
	    static createFrom(source: any = {}) {
	        return new PairingTicket(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.token = source["token"];
	        this.expires_at = source["expires_at"];
	        this.api_url = source["api_url"];
	    }
	}
	export class PlannedProvider {
	    name: string;
	    zone: string;
	
	    static createFrom(source: any = {}) {
	        return new PlannedProvider(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.zone = source["zone"];
	    }
	}
	export class PlatformNode {
	    name: string;
	    host: string;
	
	    static createFrom(source: any = {}) {
	        return new PlatformNode(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.host = source["host"];
	    }
	}
	export class Pool {
	    name: string;
	    match_tags: string[];
	    policy: string;
	
	    static createFrom(source: any = {}) {
	        return new Pool(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.match_tags = source["match_tags"];
	        this.policy = source["policy"];
	    }
	}
	export class PostChannelMessageRequest {
	    from_participant: string;
	    content: string;
	    summary?: string;
	    addressed_to?: string;
	
	    static createFrom(source: any = {}) {
	        return new PostChannelMessageRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.from_participant = source["from_participant"];
	        this.content = source["content"];
	        this.summary = source["summary"];
	        this.addressed_to = source["addressed_to"];
	    }
	}
	export class ProfileServerState {
	    name: string;
	    zone: string;
	    default_enabled: boolean;
	    override?: boolean;
	    effective: boolean;
	
	    static createFrom(source: any = {}) {
	        return new ProfileServerState(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.zone = source["zone"];
	        this.default_enabled = source["default_enabled"];
	        this.override = source["override"];
	        this.effective = source["effective"];
	    }
	}
	export class ProfileToken {
	    token_id: string;
	    token: string;
	    workspace_profile: string;
	    capabilities: string[];
	    label: string;
	
	    static createFrom(source: any = {}) {
	        return new ProfileToken(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.token_id = source["token_id"];
	        this.token = source["token"];
	        this.workspace_profile = source["workspace_profile"];
	        this.capabilities = source["capabilities"];
	        this.label = source["label"];
	    }
	}
	export class ProfileTokenInfo {
	    token_id: string;
	    workspace_profile: string;
	    capabilities: string[];
	    scope: string[];
	    label: string;
	    issued: number;
	    revoked: boolean;
	    revoked_at: number;
	    last_used: number;
	
	    static createFrom(source: any = {}) {
	        return new ProfileTokenInfo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.token_id = source["token_id"];
	        this.workspace_profile = source["workspace_profile"];
	        this.capabilities = source["capabilities"];
	        this.scope = source["scope"];
	        this.label = source["label"];
	        this.issued = source["issued"];
	        this.revoked = source["revoked"];
	        this.revoked_at = source["revoked_at"];
	        this.last_used = source["last_used"];
	    }
	}
	
	export class Rule {
	    id: string;
	    name: string;
	    profile: string;
	    enabled: boolean;
	    description: string;
	    pattern: Record<string, any>;
	    actions: any[];
	    created_at: number;
	    updated_at: number;
	    last_triggered_at?: number;
	    trigger_count: number;
	
	    static createFrom(source: any = {}) {
	        return new Rule(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.name = source["name"];
	        this.profile = source["profile"];
	        this.enabled = source["enabled"];
	        this.description = source["description"];
	        this.pattern = source["pattern"];
	        this.actions = source["actions"];
	        this.created_at = source["created_at"];
	        this.updated_at = source["updated_at"];
	        this.last_triggered_at = source["last_triggered_at"];
	        this.trigger_count = source["trigger_count"];
	    }
	}
	export class RuleEnabledState {
	    id: string;
	    enabled: boolean;
	
	    static createFrom(source: any = {}) {
	        return new RuleEnabledState(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.enabled = source["enabled"];
	    }
	}
	export class RuleExecution {
	    id: number;
	    rule_id: string;
	    event_seq: number;
	    event_id: string;
	    action_index: number;
	    action_type: string;
	    status: string;
	    attempts: number;
	    result: Record<string, any>;
	    error: string;
	    created_at: number;
	    updated_at: number;
	
	    static createFrom(source: any = {}) {
	        return new RuleExecution(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.rule_id = source["rule_id"];
	        this.event_seq = source["event_seq"];
	        this.event_id = source["event_id"];
	        this.action_index = source["action_index"];
	        this.action_type = source["action_type"];
	        this.status = source["status"];
	        this.attempts = source["attempts"];
	        this.result = source["result"];
	        this.error = source["error"];
	        this.created_at = source["created_at"];
	        this.updated_at = source["updated_at"];
	    }
	}
	export class RuleTestMatch {
	    seq: number;
	    id: string;
	    type: string;
	    status: string;
	    ts: number;
	
	    static createFrom(source: any = {}) {
	        return new RuleTestMatch(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.seq = source["seq"];
	        this.id = source["id"];
	        this.type = source["type"];
	        this.status = source["status"];
	        this.ts = source["ts"];
	    }
	}
	export class RuleTestResult {
	    valid: boolean;
	    errors: string[];
	    matched?: boolean;
	    matches?: RuleTestMatch[];
	    scanned?: number;
	
	    static createFrom(source: any = {}) {
	        return new RuleTestResult(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.valid = source["valid"];
	        this.errors = source["errors"];
	        this.matched = source["matched"];
	        this.matches = this.convertValues(source["matches"], RuleTestMatch);
	        this.scanned = source["scanned"];
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class RulesSinkStatus {
	    enabled: boolean;
	    cursor: number;
	    lag: number;
	    last_error: string;
	
	    static createFrom(source: any = {}) {
	        return new RulesSinkStatus(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.enabled = source["enabled"];
	        this.cursor = source["cursor"];
	        this.lag = source["lag"];
	        this.last_error = source["last_error"];
	    }
	}
	export class RulesStatusCounts {
	    queued: number;
	    running: number;
	    throttled: number;
	    dead: number;
	    ok_24h: number;
	
	    static createFrom(source: any = {}) {
	        return new RulesStatusCounts(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.queued = source["queued"];
	        this.running = source["running"];
	        this.throttled = source["throttled"];
	        this.dead = source["dead"];
	        this.ok_24h = source["ok_24h"];
	    }
	}
	export class RulesStatus {
	    counts: RulesStatusCounts;
	    cursor: number;
	    head_seq: number;
	    lag: number;
	    sink: RulesSinkStatus;
	
	    static createFrom(source: any = {}) {
	        return new RulesStatus(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.counts = this.convertValues(source["counts"], RulesStatusCounts);
	        this.cursor = source["cursor"];
	        this.head_seq = source["head_seq"];
	        this.lag = source["lag"];
	        this.sink = this.convertValues(source["sink"], RulesSinkStatus);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	
	export class Runner {
	    name: string;
	    capabilities: Record<string, boolean>;
	    tags: string[];
	    version: string;
	    registered_at: number;
	    last_seen: number;
	    queue_depth: number;
	    in_flight: number;
	    max_concurrent: number;
	    host: string;
	
	    static createFrom(source: any = {}) {
	        return new Runner(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.capabilities = source["capabilities"];
	        this.tags = source["tags"];
	        this.version = source["version"];
	        this.registered_at = source["registered_at"];
	        this.last_seen = source["last_seen"];
	        this.queue_depth = source["queue_depth"];
	        this.in_flight = source["in_flight"];
	        this.max_concurrent = source["max_concurrent"];
	        this.host = source["host"];
	    }
	}
	export class SearchAgentEventsOptions {
	    Q: string;
	    Type: string;
	    Workspace: string;
	    Status: string;
	    Source: string;
	    SinceMs: number;
	    UntilMs: number;
	    Limit: number;
	
	    static createFrom(source: any = {}) {
	        return new SearchAgentEventsOptions(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.Q = source["Q"];
	        this.Type = source["Type"];
	        this.Workspace = source["Workspace"];
	        this.Status = source["Status"];
	        this.Source = source["Source"];
	        this.SinceMs = source["SinceMs"];
	        this.UntilMs = source["UntilMs"];
	        this.Limit = source["Limit"];
	    }
	}
	export class SearchAgentEventsResult {
	    items: AgentEventEntry[];
	    backend: string;
	    total?: number;
	
	    static createFrom(source: any = {}) {
	        return new SearchAgentEventsResult(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.items = this.convertValues(source["items"], AgentEventEntry);
	        this.backend = source["backend"];
	        this.total = source["total"];
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class Session {
	    name: string;
	    session_name: string;
	    active: boolean;
	    role: string;
	    url: string;
	    port: any;
	    volume: string;
	    llm_provider: string;
	    llm_model: string;
	    workspace_profile: string;
	    backend: string;
	    ssh_port: any;
	    vm_state?: string;
	    runner_name?: string;
	
	    static createFrom(source: any = {}) {
	        return new Session(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.session_name = source["session_name"];
	        this.active = source["active"];
	        this.role = source["role"];
	        this.url = source["url"];
	        this.port = source["port"];
	        this.volume = source["volume"];
	        this.llm_provider = source["llm_provider"];
	        this.llm_model = source["llm_model"];
	        this.workspace_profile = source["workspace_profile"];
	        this.backend = source["backend"];
	        this.ssh_port = source["ssh_port"];
	        this.vm_state = source["vm_state"];
	        this.runner_name = source["runner_name"];
	    }
	}
	export class SessionActionResponse {
	    success: boolean;
	    error: string;
	    url: string;
	
	    static createFrom(source: any = {}) {
	        return new SessionActionResponse(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.success = source["success"];
	        this.error = source["error"];
	        this.url = source["url"];
	    }
	}
	export class SessionHistoryEntry {
	    id: number;
	    session_name: string;
	    runner_name?: string;
	    backend: string;
	    role?: string;
	    state_final: string;
	    created_at: number;
	    stopped_at: number;
	    task_id?: string;
	    job_id?: string;
	    repo_url?: string;
	    reason?: string;
	
	    static createFrom(source: any = {}) {
	        return new SessionHistoryEntry(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.session_name = source["session_name"];
	        this.runner_name = source["runner_name"];
	        this.backend = source["backend"];
	        this.role = source["role"];
	        this.state_final = source["state_final"];
	        this.created_at = source["created_at"];
	        this.stopped_at = source["stopped_at"];
	        this.task_id = source["task_id"];
	        this.job_id = source["job_id"];
	        this.repo_url = source["repo_url"];
	        this.reason = source["reason"];
	    }
	}
	export class StepPlanResult {
	    profile: string;
	    ceiling: string;
	    blocked: boolean;
	    reason: string;
	    provider?: PlannedProvider;
	    eligible_tools: string[];
	    excluded_tools: ToolZone[];
	
	    static createFrom(source: any = {}) {
	        return new StepPlanResult(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.ceiling = source["ceiling"];
	        this.blocked = source["blocked"];
	        this.reason = source["reason"];
	        this.provider = this.convertValues(source["provider"], PlannedProvider);
	        this.eligible_tools = source["eligible_tools"];
	        this.excluded_tools = this.convertValues(source["excluded_tools"], ToolZone);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class SubmitTaskRequest {
	    description: string;
	    agent_name: string;
	    repo_url?: string;
	    workspace_profile?: string;
	    workspace_home?: string;
	    pool?: string;
	
	    static createFrom(source: any = {}) {
	        return new SubmitTaskRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.description = source["description"];
	        this.agent_name = source["agent_name"];
	        this.repo_url = source["repo_url"];
	        this.workspace_profile = source["workspace_profile"];
	        this.workspace_home = source["workspace_home"];
	        this.pool = source["pool"];
	    }
	}
	
	export class Trace {
	    id: string;
	    name: string;
	    timestamp: string;
	    model: string;
	    usage: any;
	    metadata: any;
	
	    static createFrom(source: any = {}) {
	        return new Trace(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.name = source["name"];
	        this.timestamp = source["timestamp"];
	        this.model = source["model"];
	        this.usage = source["usage"];
	        this.metadata = source["metadata"];
	    }
	}
	export class TraceDetail {
	    id: string;
	    name: string;
	    timestamp: string;
	    spans: any;
	    metadata: any;
	    token_usage: any;
	
	    static createFrom(source: any = {}) {
	        return new TraceDetail(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.name = source["name"];
	        this.timestamp = source["timestamp"];
	        this.spans = source["spans"];
	        this.metadata = source["metadata"];
	        this.token_usage = source["token_usage"];
	    }
	}
	export class TrustRule {
	    pattern: string;
	    zone: string;
	
	    static createFrom(source: any = {}) {
	        return new TrustRule(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.pattern = source["pattern"];
	        this.zone = source["zone"];
	    }
	}
	export class TrustConfig {
	    profile: string;
	    default_ceiling: string;
	    rules: TrustRule[];
	
	    static createFrom(source: any = {}) {
	        return new TrustConfig(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.default_ceiling = source["default_ceiling"];
	        this.rules = this.convertValues(source["rules"], TrustRule);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	
	export class UpdateAgentRequest {
	    image?: string;
	    description?: string;
	    category?: string;
	    spawn_mode?: string;
	    capabilities?: string[];
	    hardened?: boolean;
	    persistent?: boolean;
	    role_prompt_content?: string;
	    claude_model?: string;
	    claude_effort?: string;
	    codex_model?: string;
	    ollama_model?: string;
	
	    static createFrom(source: any = {}) {
	        return new UpdateAgentRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.image = source["image"];
	        this.description = source["description"];
	        this.category = source["category"];
	        this.spawn_mode = source["spawn_mode"];
	        this.capabilities = source["capabilities"];
	        this.hardened = source["hardened"];
	        this.persistent = source["persistent"];
	        this.role_prompt_content = source["role_prompt_content"];
	        this.claude_model = source["claude_model"];
	        this.claude_effort = source["claude_effort"];
	        this.codex_model = source["codex_model"];
	        this.ollama_model = source["ollama_model"];
	    }
	}
	export class WaitForTaskRequest {
	    description: string;
	    agent_name: string;
	    repo_url?: string;
	    workspace_profile?: string;
	    workspace_home?: string;
	    timeout_sec?: number;
	
	    static createFrom(source: any = {}) {
	        return new WaitForTaskRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.description = source["description"];
	        this.agent_name = source["agent_name"];
	        this.repo_url = source["repo_url"];
	        this.workspace_profile = source["workspace_profile"];
	        this.workspace_home = source["workspace_home"];
	        this.timeout_sec = source["timeout_sec"];
	    }
	}
	export class WaitForTaskResponse {
	    task_id: string;
	    status: string;
	    result?: Record<string, any>;
	    error?: string;
	
	    static createFrom(source: any = {}) {
	        return new WaitForTaskResponse(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.task_id = source["task_id"];
	        this.status = source["status"];
	        this.result = source["result"];
	        this.error = source["error"];
	    }
	}

}

export namespace main {
	
	export class AgentInvocation {
	    prompt_args: string[];
	    prompt_mode: string;
	    accepts_cwd: boolean;
	    output_mode: string;
	
	    static createFrom(source: any = {}) {
	        return new AgentInvocation(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.prompt_args = source["prompt_args"];
	        this.prompt_mode = source["prompt_mode"];
	        this.accepts_cwd = source["accepts_cwd"];
	        this.output_mode = source["output_mode"];
	    }
	}
	export class AgentStateFilter {
	    status: string;
	    workspace: string;
	    source: string;
	    parent_id: string;
	    limit: number;
	
	    static createFrom(source: any = {}) {
	        return new AgentStateFilter(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.status = source["status"];
	        this.workspace = source["workspace"];
	        this.source = source["source"];
	        this.parent_id = source["parent_id"];
	        this.limit = source["limit"];
	    }
	}
	export class ArtifactPreview {
	    kind: string;
	    content_type: string;
	    size: number;
	    text?: string;
	    data_uri?: string;
	    truncated: boolean;
	    reason?: string;
	
	    static createFrom(source: any = {}) {
	        return new ArtifactPreview(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.kind = source["kind"];
	        this.content_type = source["content_type"];
	        this.size = source["size"];
	        this.text = source["text"];
	        this.data_uri = source["data_uri"];
	        this.truncated = source["truncated"];
	        this.reason = source["reason"];
	    }
	}
	export class AttentionItem {
	    id: string;
	    source: string;
	    source_id: string;
	    status: string;
	    title: string;
	    subtitle: string;
	    reason: string;
	    workspace: string;
	    time: number;
	    url?: string;
	    actions: string[];
	    user_reply?: string;
	    session_name?: string;
	    runner_name?: string;
	
	    static createFrom(source: any = {}) {
	        return new AttentionItem(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.source = source["source"];
	        this.source_id = source["source_id"];
	        this.status = source["status"];
	        this.title = source["title"];
	        this.subtitle = source["subtitle"];
	        this.reason = source["reason"];
	        this.workspace = source["workspace"];
	        this.time = source["time"];
	        this.url = source["url"];
	        this.actions = source["actions"];
	        this.user_reply = source["user_reply"];
	        this.session_name = source["session_name"];
	        this.runner_name = source["runner_name"];
	    }
	}
	export class AutomationRule {
	    id: string;
	    profile: string;
	    name: string;
	    description: string;
	    enabled: boolean;
	    trigger_type: string;
	    trigger_config: string;
	    action_type: string;
	    action_config: string;
	    created_at: number;
	    last_triggered_at?: number;
	    trigger_count: number;
	
	    static createFrom(source: any = {}) {
	        return new AutomationRule(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.profile = source["profile"];
	        this.name = source["name"];
	        this.description = source["description"];
	        this.enabled = source["enabled"];
	        this.trigger_type = source["trigger_type"];
	        this.trigger_config = source["trigger_config"];
	        this.action_type = source["action_type"];
	        this.action_config = source["action_config"];
	        this.created_at = source["created_at"];
	        this.last_triggered_at = source["last_triggered_at"];
	        this.trigger_count = source["trigger_count"];
	    }
	}
	export class BaseImageBuildRequest {
	    profile: string;
	    no_cache: boolean;
	
	    static createFrom(source: any = {}) {
	        return new BaseImageBuildRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.no_cache = source["no_cache"];
	    }
	}
	export class BundleSourceView {
	    name: string;
	    label: string;
	    kind: string;
	    audience: string;
	    enabled: boolean;
	    detected: boolean;
	    definition: string;
	
	    static createFrom(source: any = {}) {
	        return new BundleSourceView(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.label = source["label"];
	        this.kind = source["kind"];
	        this.audience = source["audience"];
	        this.enabled = source["enabled"];
	        this.detected = source["detected"];
	        this.definition = source["definition"];
	    }
	}
	export class CollectJob {
	    id: string;
	    profile: string;
	    name: string;
	    command: string;
	    interval_s: number;
	    enabled: boolean;
	    default_actions: string;
	    last_run_at?: number;
	    last_error: string;
	    created_at: number;
	    target_type: string;
	    target_id: string;
	    target_prompt: string;
	    run_at: string;
	    days: string;
	    source: string;
	    owner_widget_id: string;
	
	    static createFrom(source: any = {}) {
	        return new CollectJob(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.profile = source["profile"];
	        this.name = source["name"];
	        this.command = source["command"];
	        this.interval_s = source["interval_s"];
	        this.enabled = source["enabled"];
	        this.default_actions = source["default_actions"];
	        this.last_run_at = source["last_run_at"];
	        this.last_error = source["last_error"];
	        this.created_at = source["created_at"];
	        this.target_type = source["target_type"];
	        this.target_id = source["target_id"];
	        this.target_prompt = source["target_prompt"];
	        this.run_at = source["run_at"];
	        this.days = source["days"];
	        this.source = source["source"];
	        this.owner_widget_id = source["owner_widget_id"];
	    }
	}
	export class CollectedEntry {
	    job_id: string;
	    entry_id: string;
	    profile: string;
	    kind: string;
	    title: string;
	    description: string;
	    value: string;
	    url: string;
	    start_at?: number;
	    end_at?: number;
	    status: string;
	    tags: string[];
	    metadata: number[];
	    actions: number[];
	    collected_at: number;
	
	    static createFrom(source: any = {}) {
	        return new CollectedEntry(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.job_id = source["job_id"];
	        this.entry_id = source["entry_id"];
	        this.profile = source["profile"];
	        this.kind = source["kind"];
	        this.title = source["title"];
	        this.description = source["description"];
	        this.value = source["value"];
	        this.url = source["url"];
	        this.start_at = source["start_at"];
	        this.end_at = source["end_at"];
	        this.status = source["status"];
	        this.tags = source["tags"];
	        this.metadata = source["metadata"];
	        this.actions = source["actions"];
	        this.collected_at = source["collected_at"];
	    }
	}
	export class Config {
	    base_url: string;
	    api_key: string;
	    active_profile: string;
	    workspaces_root: string;
	    theme: string;
	
	    static createFrom(source: any = {}) {
	        return new Config(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.base_url = source["base_url"];
	        this.api_key = source["api_key"];
	        this.active_profile = source["active_profile"];
	        this.workspaces_root = source["workspaces_root"];
	        this.theme = source["theme"];
	    }
	}
	export class ContainerDiskStat {
	    name: string;
	    writable_size: string;
	    writable_size_bytes: number;
	    virtual_size: string;
	
	    static createFrom(source: any = {}) {
	        return new ContainerDiskStat(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.writable_size = source["writable_size"];
	        this.writable_size_bytes = source["writable_size_bytes"];
	        this.virtual_size = source["virtual_size"];
	    }
	}
	export class ContainerStat {
	    name: string;
	    id: string;
	    cpu_perc: string;
	    mem_usage: string;
	    mem_perc: string;
	    net_io: string;
	    block_io: string;
	    pids: string;
	
	    static createFrom(source: any = {}) {
	        return new ContainerStat(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.id = source["id"];
	        this.cpu_perc = source["cpu_perc"];
	        this.mem_usage = source["mem_usage"];
	        this.mem_perc = source["mem_perc"];
	        this.net_io = source["net_io"];
	        this.block_io = source["block_io"];
	        this.pids = source["pids"];
	    }
	}
	export class DatabaseInfo {
	    name: string;
	    size: string;
	
	    static createFrom(source: any = {}) {
	        return new DatabaseInfo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.size = source["size"];
	    }
	}
	export class DetectedAgent {
	    id: string;
	    binary: string;
	    label: string;
	    path: string;
	    version: string;
	    enabled: boolean;
	    detected: boolean;
	    detected_at: string;
	    invocation: AgentInvocation;
	
	    static createFrom(source: any = {}) {
	        return new DetectedAgent(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.binary = source["binary"];
	        this.label = source["label"];
	        this.path = source["path"];
	        this.version = source["version"];
	        this.enabled = source["enabled"];
	        this.detected = source["detected"];
	        this.detected_at = source["detected_at"];
	        this.invocation = this.convertValues(source["invocation"], AgentInvocation);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class DiskCategory {
	    name: string;
	    bytes: number;
	    label: string;
	
	    static createFrom(source: any = {}) {
	        return new DiskCategory(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.bytes = source["bytes"];
	        this.label = source["label"];
	    }
	}
	export class DiskBreakdown {
	    total_bytes: number;
	    total_label: string;
	    categories: DiskCategory[];
	
	    static createFrom(source: any = {}) {
	        return new DiskBreakdown(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.total_bytes = source["total_bytes"];
	        this.total_label = source["total_label"];
	        this.categories = this.convertValues(source["categories"], DiskCategory);
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	
	export class ProfileDiskUsage {
	    name: string;
	    bytes: number;
	    label: string;
	
	    static createFrom(source: any = {}) {
	        return new ProfileDiskUsage(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.bytes = source["bytes"];
	        this.label = source["label"];
	    }
	}
	export class DiskOverview {
	    total_disk: number;
	    total_label: string;
	    used_disk: number;
	    used_label: string;
	    profiles: ProfileDiskUsage[];
	    os_bytes: number;
	    os_label: string;
	    scanned_at: string;
	
	    static createFrom(source: any = {}) {
	        return new DiskOverview(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.total_disk = source["total_disk"];
	        this.total_label = source["total_label"];
	        this.used_disk = source["used_disk"];
	        this.used_label = source["used_label"];
	        this.profiles = this.convertValues(source["profiles"], ProfileDiskUsage);
	        this.os_bytes = source["os_bytes"];
	        this.os_label = source["os_label"];
	        this.scanned_at = source["scanned_at"];
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	export class EnqueueTaskRequest {
	    loop_id: string;
	    input: string;
	    cwd: string;
	    priority: number;
	    max_attempts: number;
	    trigger: string;
	    parent_task_id: string;
	    workspace_profile: string;
	    scheduled_for: string;
	
	    static createFrom(source: any = {}) {
	        return new EnqueueTaskRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.loop_id = source["loop_id"];
	        this.input = source["input"];
	        this.cwd = source["cwd"];
	        this.priority = source["priority"];
	        this.max_attempts = source["max_attempts"];
	        this.trigger = source["trigger"];
	        this.parent_task_id = source["parent_task_id"];
	        this.workspace_profile = source["workspace_profile"];
	        this.scheduled_for = source["scheduled_for"];
	    }
	}
	export class HubTask {
	    id: string;
	    description: string;
	    agent_name: string;
	    status: string;
	    repo_url: string;
	    created_at: number;
	    updated_at: number;
	    result: string;
	    error: string;
	    session_name: string;
	    workspace_profile: string;
	    job_id: string;
	    spawned_by: string;
	    child_task_ids: string[];
	    channel_ids: string[];
	
	    static createFrom(source: any = {}) {
	        return new HubTask(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.description = source["description"];
	        this.agent_name = source["agent_name"];
	        this.status = source["status"];
	        this.repo_url = source["repo_url"];
	        this.created_at = source["created_at"];
	        this.updated_at = source["updated_at"];
	        this.result = source["result"];
	        this.error = source["error"];
	        this.session_name = source["session_name"];
	        this.workspace_profile = source["workspace_profile"];
	        this.job_id = source["job_id"];
	        this.spawned_by = source["spawned_by"];
	        this.child_task_ids = source["child_task_ids"];
	        this.channel_ids = source["channel_ids"];
	    }
	}
	export class HubStateView {
	    agents: brainbox.AgentDefinition[];
	    tasks: HubTask[];
	    tokens: any[];
	
	    static createFrom(source: any = {}) {
	        return new HubStateView(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.agents = this.convertValues(source["agents"], brainbox.AgentDefinition);
	        this.tasks = this.convertValues(source["tasks"], HubTask);
	        this.tokens = source["tokens"];
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	
	export class IntegrationPlacementOutcome {
	    ok: boolean;
	    name: string;
	    node: string;
	    desired: string;
	    endpoint: string;
	    output: string;
	    wired: string[];
	    wire_err: string;
	
	    static createFrom(source: any = {}) {
	        return new IntegrationPlacementOutcome(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.ok = source["ok"];
	        this.name = source["name"];
	        this.node = source["node"];
	        this.desired = source["desired"];
	        this.endpoint = source["endpoint"];
	        this.output = source["output"];
	        this.wired = source["wired"];
	        this.wire_err = source["wire_err"];
	    }
	}
	export class LocalProcess {
	    pid: string;
	    tty: string;
	    command: string;
	    name: string;
	    cpu_perc: string;
	    mem_mb: string;
	    workspace_profile: string;
	    workspace_home: string;
	
	    static createFrom(source: any = {}) {
	        return new LocalProcess(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.pid = source["pid"];
	        this.tty = source["tty"];
	        this.command = source["command"];
	        this.name = source["name"];
	        this.cpu_perc = source["cpu_perc"];
	        this.mem_mb = source["mem_mb"];
	        this.workspace_profile = source["workspace_profile"];
	        this.workspace_home = source["workspace_home"];
	    }
	}
	export class LocalRunnerStatus {
	    enabled: boolean;
	    running: boolean;
	    name: string;
	
	    static createFrom(source: any = {}) {
	        return new LocalRunnerStatus(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.enabled = source["enabled"];
	        this.running = source["running"];
	        this.name = source["name"];
	    }
	}
	export class LogEntry {
	    line: string;
	
	    static createFrom(source: any = {}) {
	        return new LogEntry(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.line = source["line"];
	    }
	}
	export class OpenTarget {
	    panel: string;
	    ref: string;
	
	    static createFrom(source: any = {}) {
	        return new OpenTarget(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.panel = source["panel"];
	        this.ref = source["ref"];
	    }
	}
	export class PlatformExternal {
	    name: string;
	    label: string;
	    endpoint: string;
	    healthy: boolean;
	    note: string;
	
	    static createFrom(source: any = {}) {
	        return new PlatformExternal(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.label = source["label"];
	        this.endpoint = source["endpoint"];
	        this.healthy = source["healthy"];
	        this.note = source["note"];
	    }
	}
	export class PlatformService {
	    name: string;
	    state: string;
	    status: string;
	    health: string;
	    one_shot: boolean;
	    addr?: string;
	    web_url?: string;
	
	    static createFrom(source: any = {}) {
	        return new PlatformService(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.state = source["state"];
	        this.status = source["status"];
	        this.health = source["health"];
	        this.one_shot = source["one_shot"];
	        this.addr = source["addr"];
	        this.web_url = source["web_url"];
	    }
	}
	export class PreflightCheck {
	    name: string;
	    status: string;
	    message: string;
	
	    static createFrom(source: any = {}) {
	        return new PreflightCheck(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.status = source["status"];
	        this.message = source["message"];
	    }
	}
	export class Profile {
	    name: string;
	    path: string;
	    workspace_home: string;
	    has_secrets: boolean;
	    secrets_mode: string;
	    secrets_path: string;
	    has_backup: boolean;
	
	    static createFrom(source: any = {}) {
	        return new Profile(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.path = source["path"];
	        this.workspace_home = source["workspace_home"];
	        this.has_secrets = source["has_secrets"];
	        this.secrets_mode = source["secrets_mode"];
	        this.secrets_path = source["secrets_path"];
	        this.has_backup = source["has_backup"];
	    }
	}
	
	export class ProfileImageBuildRequest {
	    profile: string;
	    base_image: string;
	    registry_url: string;
	    no_cache: boolean;
	
	    static createFrom(source: any = {}) {
	        return new ProfileImageBuildRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.base_image = source["base_image"];
	        this.registry_url = source["registry_url"];
	        this.no_cache = source["no_cache"];
	    }
	}
	export class ProfileImageRow {
	    profile: string;
	    registry_url: string;
	    last_pushed_at: string;
	    last_digest: string;
	    env_key?: string;
	
	    static createFrom(source: any = {}) {
	        return new ProfileImageRow(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.profile = source["profile"];
	        this.registry_url = source["registry_url"];
	        this.last_pushed_at = source["last_pushed_at"];
	        this.last_digest = source["last_digest"];
	        this.env_key = source["env_key"];
	    }
	}
	export class ScheduleRow {
	    id: string;
	    loop_id: string;
	    cron_expr: string;
	    input: string;
	    cwd: string;
	    enabled: boolean;
	    workspace_profile: string;
	    created_at: string;
	    updated_at: string;
	    last_fired_at: string;
	    next_fire_at: string;
	
	    static createFrom(source: any = {}) {
	        return new ScheduleRow(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.loop_id = source["loop_id"];
	        this.cron_expr = source["cron_expr"];
	        this.input = source["input"];
	        this.cwd = source["cwd"];
	        this.enabled = source["enabled"];
	        this.workspace_profile = source["workspace_profile"];
	        this.created_at = source["created_at"];
	        this.updated_at = source["updated_at"];
	        this.last_fired_at = source["last_fired_at"];
	        this.next_fire_at = source["next_fire_at"];
	    }
	}
	export class SecretKeyStatus {
	    key: string;
	    has_value: boolean;
	    source: string;
	
	    static createFrom(source: any = {}) {
	        return new SecretKeyStatus(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.key = source["key"];
	        this.has_value = source["has_value"];
	        this.source = source["source"];
	    }
	}
	export class SequenceFollowup {
	    loop_id: string;
	    input_from: string;
	    input_literal: string;
	    cwd: string;
	
	    static createFrom(source: any = {}) {
	        return new SequenceFollowup(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.loop_id = source["loop_id"];
	        this.input_from = source["input_from"];
	        this.input_literal = source["input_literal"];
	        this.cwd = source["cwd"];
	    }
	}
	export class SequenceStep {
	    type: string;
	    agent_id: string;
	    prompt_template: string;
	    cwd: string;
	    executor: string;
	
	    static createFrom(source: any = {}) {
	        return new SequenceStep(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.type = source["type"];
	        this.agent_id = source["agent_id"];
	        this.prompt_template = source["prompt_template"];
	        this.cwd = source["cwd"];
	        this.executor = source["executor"];
	    }
	}
	export class Sequence {
	    id: string;
	    name: string;
	    description: string;
	    steps: SequenceStep[];
	    cwd: string;
	    on_success: SequenceFollowup[];
	    files: string[];
	    workspace_profile: string;
	    created_at: string;
	    updated_at: string;
	
	    static createFrom(source: any = {}) {
	        return new Sequence(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.name = source["name"];
	        this.description = source["description"];
	        this.steps = this.convertValues(source["steps"], SequenceStep);
	        this.cwd = source["cwd"];
	        this.on_success = this.convertValues(source["on_success"], SequenceFollowup);
	        this.files = source["files"];
	        this.workspace_profile = source["workspace_profile"];
	        this.created_at = source["created_at"];
	        this.updated_at = source["updated_at"];
	    }
	
		convertValues(a: any, classs: any, asMap: boolean = false): any {
		    if (!a) {
		        return a;
		    }
		    if (a.slice && a.map) {
		        return (a as any[]).map(elem => this.convertValues(elem, classs));
		    } else if ("object" === typeof a) {
		        if (asMap) {
		            for (const key of Object.keys(a)) {
		                a[key] = new classs(a[key]);
		            }
		            return a;
		        }
		        return new classs(a);
		    }
		    return a;
		}
	}
	
	export class SequenceRunRow {
	    id: string;
	    loop_id: string;
	    started_at: string;
	    finished_at: string;
	    status: string;
	    log_json: string;
	
	    static createFrom(source: any = {}) {
	        return new SequenceRunRow(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.loop_id = source["loop_id"];
	        this.started_at = source["started_at"];
	        this.finished_at = source["finished_at"];
	        this.status = source["status"];
	        this.log_json = source["log_json"];
	    }
	}
	
	export class ServiceStatus {
	    name: string;
	    label: string;
	    description: string;
	    default_url: string;
	    port: number;
	    native: boolean;
	    platform: boolean;
	    enabled: boolean;
	    remote: boolean;
	    local_url: string;
	    remote_url: string;
	    url: string;
	    running: boolean;
	
	    static createFrom(source: any = {}) {
	        return new ServiceStatus(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.label = source["label"];
	        this.description = source["description"];
	        this.default_url = source["default_url"];
	        this.port = source["port"];
	        this.native = source["native"];
	        this.platform = source["platform"];
	        this.enabled = source["enabled"];
	        this.remote = source["remote"];
	        this.local_url = source["local_url"];
	        this.remote_url = source["remote_url"];
	        this.url = source["url"];
	        this.running = source["running"];
	    }
	}
	export class SystemInfo {
	    cpu_cores: number;
	    mem_total_bytes: number;
	    mem_total_gib: number;
	
	    static createFrom(source: any = {}) {
	        return new SystemInfo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.cpu_cores = source["cpu_cores"];
	        this.mem_total_bytes = source["mem_total_bytes"];
	        this.mem_total_gib = source["mem_total_gib"];
	    }
	}
	export class TaskRow {
	    id: string;
	    loop_id: string;
	    status: string;
	    priority: number;
	    input: string;
	    cwd: string;
	    trigger: string;
	    parent_task_id: string;
	    workspace_profile: string;
	    enqueued_at: string;
	    scheduled_for: string;
	    started_at: string;
	    finished_at: string;
	    attempts: number;
	    max_attempts: number;
	    last_error: string;
	    result_run_id: string;
	
	    static createFrom(source: any = {}) {
	        return new TaskRow(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.loop_id = source["loop_id"];
	        this.status = source["status"];
	        this.priority = source["priority"];
	        this.input = source["input"];
	        this.cwd = source["cwd"];
	        this.trigger = source["trigger"];
	        this.parent_task_id = source["parent_task_id"];
	        this.workspace_profile = source["workspace_profile"];
	        this.enqueued_at = source["enqueued_at"];
	        this.scheduled_for = source["scheduled_for"];
	        this.started_at = source["started_at"];
	        this.finished_at = source["finished_at"];
	        this.attempts = source["attempts"];
	        this.max_attempts = source["max_attempts"];
	        this.last_error = source["last_error"];
	        this.result_run_id = source["result_run_id"];
	    }
	}
	export class TaskStats {
	    window_hours: number;
	    pending: number;
	    running: number;
	    succeeded: number;
	    failed: number;
	    cancelled: number;
	
	    static createFrom(source: any = {}) {
	        return new TaskStats(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.window_hours = source["window_hours"];
	        this.pending = source["pending"];
	        this.running = source["running"];
	        this.succeeded = source["succeeded"];
	        this.failed = source["failed"];
	        this.cancelled = source["cancelled"];
	    }
	}
	export class UpcomingFire {
	    schedule_id: string;
	    loop_id: string;
	    loop_name: string;
	    cron_expr: string;
	    next_fire_at: string;
	
	    static createFrom(source: any = {}) {
	        return new UpcomingFire(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.schedule_id = source["schedule_id"];
	        this.loop_id = source["loop_id"];
	        this.loop_name = source["loop_name"];
	        this.cron_expr = source["cron_expr"];
	        this.next_fire_at = source["next_fire_at"];
	    }
	}

}

export namespace opensearch {
	
	export class LogEntry {
	    time: string;
	    body: string;
	    session?: string;
	    workspace?: string;
	    model?: string;
	    duration_ms?: number;
	
	    static createFrom(source: any = {}) {
	        return new LogEntry(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.time = source["time"];
	        this.body = source["body"];
	        this.session = source["session"];
	        this.workspace = source["workspace"];
	        this.model = source["model"];
	        this.duration_ms = source["duration_ms"];
	    }
	}
	export class Overview {
	    cost_today_usd: number;
	    tokens_today: number;
	    api_requests_1h: number;
	    avg_latency_ms_1h: number;
	    as_of: string;
	    workspace: string;
	    matched_workspace: boolean;
	
	    static createFrom(source: any = {}) {
	        return new Overview(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.cost_today_usd = source["cost_today_usd"];
	        this.tokens_today = source["tokens_today"];
	        this.api_requests_1h = source["api_requests_1h"];
	        this.avg_latency_ms_1h = source["avg_latency_ms_1h"];
	        this.as_of = source["as_of"];
	        this.workspace = source["workspace"];
	        this.matched_workspace = source["matched_workspace"];
	    }
	}

}

