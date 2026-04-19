export namespace brainbox {
	
	export class AddRepoRequest {
	    url: string;
	    name?: string;
	    merge_queue?: boolean;
	    pr_shepherd?: boolean;
	    target_branch?: string;
	    is_fork?: boolean;
	    upstream_url?: string;
	    workspace_profile?: string;
	    workspace_home?: string;
	
	    static createFrom(source: any = {}) {
	        return new AddRepoRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.url = source["url"];
	        this.name = source["name"];
	        this.merge_queue = source["merge_queue"];
	        this.pr_shepherd = source["pr_shepherd"];
	        this.target_branch = source["target_branch"];
	        this.is_fork = source["is_fork"];
	        this.upstream_url = source["upstream_url"];
	        this.workspace_profile = source["workspace_profile"];
	        this.workspace_home = source["workspace_home"];
	    }
	}
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
	export class Artifact {
	    key: string;
	    size: number;
	    last_modified: string;
	    content_type: string;
	
	    static createFrom(source: any = {}) {
	        return new Artifact(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.key = source["key"];
	        this.size = source["size"];
	        this.last_modified = source["last_modified"];
	        this.content_type = source["content_type"];
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
	
	    static createFrom(source: any = {}) {
	        return new CreateChannelRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.participants = this.convertValues(source["participants"], ChannelParticipantRequest);
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
	export class CreatePlaybookRequest {
	    name: string;
	    markdown: string;
	    workspace_profile?: string;
	
	    static createFrom(source: any = {}) {
	        return new CreatePlaybookRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.markdown = source["markdown"];
	        this.workspace_profile = source["workspace_profile"];
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
	    ports?: Record<string, number>;
	    docker_host?: string;
	
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
	        this.ports = source["ports"];
	        this.docker_host = source["docker_host"];
	    }
	}
	export class CreateWorktreeRequest {
	    repo_name: string;
	    branch: string;
	
	    static createFrom(source: any = {}) {
	        return new CreateWorktreeRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.repo_name = source["repo_name"];
	        this.branch = source["branch"];
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
	export class Repo {
	    name: string;
	    url: string;
	    merge_queue_enabled: boolean;
	    pr_shepherd_enabled: boolean;
	    target_branch: string;
	    is_fork: boolean;
	    upstream_url: string;
	    workspace_profile: string;
	    workspace_home: string;
	
	    static createFrom(source: any = {}) {
	        return new Repo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.name = source["name"];
	        this.url = source["url"];
	        this.merge_queue_enabled = source["merge_queue_enabled"];
	        this.pr_shepherd_enabled = source["pr_shepherd_enabled"];
	        this.target_branch = source["target_branch"];
	        this.is_fork = source["is_fork"];
	        this.upstream_url = source["upstream_url"];
	        this.workspace_profile = source["workspace_profile"];
	        this.workspace_home = source["workspace_home"];
	    }
	}
	export class Task {
	    id: string;
	    description: string;
	    agent_name: string;
	    status: string;
	    repo_url: any;
	    created_at: any;
	    updated_at: any;
	    result: any;
	    error: any;
	    session_name: string;
	    workspace_profile: string;
	
	    static createFrom(source: any = {}) {
	        return new Task(source);
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
	    }
	}
	export class HubState {
	    agents: AgentDefinition[];
	    tasks: Task[];
	    tokens: any[];
	    repos: Repo[];
	
	    static createFrom(source: any = {}) {
	        return new HubState(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.agents = this.convertValues(source["agents"], AgentDefinition);
	        this.tasks = this.convertValues(source["tasks"], Task);
	        this.tokens = source["tokens"];
	        this.repos = this.convertValues(source["repos"], Repo);
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
	export class PlaybookTask {
	    id: string;
	    index: number;
	    content: string;
	    status: string;
	    session_name?: string;
	    output?: string;
	    error?: string;
	    started_at?: number;
	    finished_at?: number;
	
	    static createFrom(source: any = {}) {
	        return new PlaybookTask(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.index = source["index"];
	        this.content = source["content"];
	        this.status = source["status"];
	        this.session_name = source["session_name"];
	        this.output = source["output"];
	        this.error = source["error"];
	        this.started_at = source["started_at"];
	        this.finished_at = source["finished_at"];
	    }
	}
	export class Playbook {
	    id: string;
	    name: string;
	    markdown: string;
	    tasks: PlaybookTask[];
	    status: string;
	    workspace_profile: string;
	    created_at: number;
	    started_at?: number;
	    finished_at?: number;
	
	    static createFrom(source: any = {}) {
	        return new Playbook(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.name = source["name"];
	        this.markdown = source["markdown"];
	        this.tasks = this.convertValues(source["tasks"], PlaybookTask);
	        this.status = source["status"];
	        this.workspace_profile = source["workspace_profile"];
	        this.created_at = source["created_at"];
	        this.started_at = source["started_at"];
	        this.finished_at = source["finished_at"];
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
	export class SubmitTaskRequest {
	    description: string;
	    agent_name: string;
	    repo_url?: string;
	    workspace_profile?: string;
	    workspace_home?: string;
	
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
	export class UpdateRepoRequest {
	    merge_queue?: boolean;
	    pr_shepherd?: boolean;
	    target_branch?: string;
	
	    static createFrom(source: any = {}) {
	        return new UpdateRepoRequest(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.merge_queue = source["merge_queue"];
	        this.pr_shepherd = source["pr_shepherd"];
	        this.target_branch = source["target_branch"];
	    }
	}
	export class Worktree {
	    id: string;
	    repo_name: string;
	    branch: string;
	    worktree_path: string;
	    session_name?: string;
	    status: string;
	    created_at: number;
	    error?: string;
	
	    static createFrom(source: any = {}) {
	        return new Worktree(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.repo_name = source["repo_name"];
	        this.branch = source["branch"];
	        this.worktree_path = source["worktree_path"];
	        this.session_name = source["session_name"];
	        this.status = source["status"];
	        this.created_at = source["created_at"];
	        this.error = source["error"];
	    }
	}
	export class WorktreeSessionResponse {
	    worktree_id: string;
	    session: string;
	
	    static createFrom(source: any = {}) {
	        return new WorktreeSessionResponse(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.worktree_id = source["worktree_id"];
	        this.session = source["session"];
	    }
	}

}

export namespace main {
	
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
	export class ServiceStatus {
	    name: string;
	    label: string;
	    description: string;
	    default_url: string;
	    port: number;
	    native: boolean;
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

}

