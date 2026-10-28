//! Explicit, bounded native grants for named HTTP destinations and shared objects.

use crate::{
    Error,
    error::Result,
    value::{Type, Value, ValueKind},
};
use futures_util::StreamExt as _;
use serde::Deserialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    net::{IpAddr, SocketAddr},
    path::PathBuf,
    time::Duration,
};
use zeroize::Zeroizing;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Configuration {
    objects: Option<PathBuf>,
    oauth: Option<OAuthConfiguration>,
    #[serde(default)]
    git: BTreeMap<String, GitGrant>,
    #[serde(default)]
    delivery: BTreeMap<String, DeliveryGrant>,
    #[serde(default)]
    operations: BTreeMap<String, OperationsGrant>,
    #[serde(default)]
    mail: BTreeMap<String, MailGrant>,
    #[serde(default)]
    http: BTreeMap<String, Destination>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OAuthConfiguration {
    transaction_key_env: String,
    google: Option<OAuthClientConfiguration>,
    github: Option<OAuthClientConfiguration>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OAuthClientConfiguration {
    client_id: String,
    client_secret_env: String,
}

pub(crate) struct OAuthRuntime {
    pub(crate) transaction_key: Zeroizing<[u8; 32]>,
    pub(crate) google: Option<OAuthClient>,
    pub(crate) github: Option<OAuthClient>,
}

pub(crate) struct OAuthClient {
    pub(crate) client_id: String,
    pub(crate) client_secret: Zeroizing<String>,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct GitGrant {
    pub(crate) root: PathBuf,
    pub(crate) executable: PathBuf,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct DeliveryGrant {
    pub(crate) root: PathBuf,
    pub(crate) sandbox: PathBuf,
    pub(crate) limit: PathBuf,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct OperationsGrant {
    pub(crate) root: PathBuf,
}
#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MailGrant {
    pub(crate) root: PathBuf,
    pub(crate) from: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Destination {
    origin: String,
    network: Network,
    paths: Vec<String>,
    methods: BTreeSet<String>,
    credential_env: Option<String>,
    #[serde(default = "deadline")]
    timeout_ms: u64,
    #[serde(default = "output_limit")]
    max_response_bytes: usize,
    #[serde(default)]
    max_redirects: usize,
    ca_file: Option<PathBuf>,
    dns_server: Option<SocketAddr>,
    #[serde(skip)]
    dns: Option<DnsConfiguration>,
    #[serde(skip)]
    certificate: Option<reqwest::Certificate>,
    #[serde(skip)]
    credential: Option<Zeroizing<String>>,
    #[serde(skip)]
    generation: String,
}
#[derive(Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
enum Network {
    Public,
    Loopback,
}
const fn deadline() -> u64 {
    3000
}
const fn output_limit() -> usize {
    65536
}

/// Native runtime authority. Contents are private and never serialized in builds.
/// Construct this outside managed Diamond code and pass it explicitly when serving.
#[derive(Default)]
pub struct RuntimeGrants {
    pub(crate) objects: Option<PathBuf>,
    pub(crate) git: BTreeMap<String, GitGrant>,
    pub(crate) delivery: BTreeMap<String, DeliveryGrant>,
    pub(crate) operations: BTreeMap<String, OperationsGrant>,
    pub(crate) mail: BTreeMap<String, MailGrant>,
    pub(crate) oauth: Option<OAuthRuntime>,
    destinations: BTreeMap<String, Destination>,
}
impl fmt::Debug for RuntimeGrants {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("RuntimeGrants([PRIVATE])")
    }
}
impl RuntimeGrants {
    /// Parse bounded native JSON configuration and capture named environment secrets.
    /// An omitted credential permits only an unauthenticated destination.
    ///
    /// # Errors
    /// Rejects invalid origins, bounds, network profiles, and missing credentials.
    /// Diagnostic messages never include configuration or secret contents.
    ///
    /// ```
    /// let grants = diamond::RuntimeGrants::from_json(r#"{"http":{}}"#)?;
    /// assert_eq!(format!("{grants:?}"), "RuntimeGrants([PRIVATE])");
    /// # Ok::<(), diamond::Error>(())
    /// ```
    pub fn from_json(source: &str) -> Result<Self> {
        if source.len() > 65536 {
            return Err(Error::Limit("runtime configuration"));
        }
        let mut config: Configuration =
            crate::project::wire::strict_from_slice(source.as_bytes()).map_err(|_| invalid())?;
        if config.http.len() > 16 {
            return Err(invalid());
        }
        validate_native_grants(&config)?;
        for (name, dest) in &mut config.http {
            let origin = url::Url::parse(&dest.origin).map_err(|_| invalid())?;
            if !crate::project::resolve::identifier(name)
                || origin.origin().ascii_serialization() != dest.origin
                || origin.path() != "/"
                || origin.query().is_some()
                || origin.fragment().is_some()
                || !origin.username().is_empty()
                || origin.password().is_some()
                || !matches!(origin.scheme(), "https" | "http")
                || (matches!(dest.network, Network::Public) && origin.scheme() != "https")
                || !(1..=5000).contains(&dest.timeout_ms)
                || !(1..=crate::MAX_SOURCE_BYTES).contains(&dest.max_response_bytes)
                || dest.max_redirects > 2
                || dest.paths.is_empty()
                || dest.paths.len() > 16
                || dest.paths.iter().any(|p| !valid_path(p) || p.contains('?'))
                || dest.methods.is_empty()
                || dest.methods.iter().any(|m| m != "GET" && m != "POST")
            {
                return Err(invalid());
            }
            if let Some(var) = &dest.credential_env {
                if var.is_empty()
                    || var.len() > 128
                    || !var.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
                {
                    return Err(invalid());
                }
                let value = Zeroizing::new(std::env::var(var).map_err(|_| invalid())?);
                if value.is_empty()
                    || value.len() > 4096
                    || !value.bytes().all(|b| (33..=126).contains(&b))
                {
                    return Err(invalid());
                }
                dest.credential = Some(value);
            }
            if let Some(path) = &dest.ca_file {
                use std::io::Read as _;
                let mut options = std::fs::OpenOptions::new();
                options.read(true);
                #[cfg(unix)]
                {
                    use std::os::unix::fs::OpenOptionsExt;
                    options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
                }
                let file = options.open(path).map_err(|_| invalid())?;
                if !file.metadata().map_err(|_| invalid())?.is_file() {
                    return Err(invalid());
                }
                let mut bytes = Vec::new();
                file.take(65537)
                    .read_to_end(&mut bytes)
                    .map_err(|_| invalid())?;
                if bytes.len() > 65536 {
                    return Err(invalid());
                }
                dest.certificate =
                    Some(reqwest::Certificate::from_pem(&bytes).map_err(|_| invalid())?);
            }
            dest.dns = Some(DnsConfiguration::capture(dest)?);
            dest.generation = crate::authority::random_hex()?;
        }
        if config.objects.as_ref().is_some_and(|p| !p.is_absolute()) {
            return Err(invalid());
        }
        let oauth = config.oauth.map(OAuthRuntime::capture).transpose()?;
        Ok(Self {
            objects: config.objects,
            git: config.git,
            delivery: config.delivery,
            operations: config.operations,
            mail: config.mail,
            oauth,
            destinations: config.http,
        })
    }
    pub(crate) fn require(&self, name: &str) -> Result<()> {
        if self.destinations.contains_key(name) {
            Ok(())
        } else {
            Err(Error::Denied)
        }
    }
    pub(crate) fn has_http_credential(&self, name: &str) -> bool {
        self.destinations
            .get(name)
            .is_some_and(|destination| destination.credential.is_some())
    }
    pub(crate) fn has_oauth_client(&self, provider: &str) -> bool {
        self.oauth
            .as_ref()
            .and_then(|oauth| oauth.client(provider))
            .is_some()
    }
    pub(crate) fn secret(&self, name: &str) -> Result<Value> {
        let d = self.destinations.get(name).ok_or(Error::Denied)?;
        Ok(Value(ValueKind::Secret(SecretHandle {
            name: name.into(),
            generation: d.generation.clone(),
        })))
    }
    pub(crate) fn call(
        &self,
        call: Call<'_>,
        result: &Type,
        program: &crate::hir::CheckedProgram,
    ) -> Result<Value> {
        let Call {
            method,
            name,
            path,
            handle,
            body,
        } = call;
        let d = self.destinations.get(name).ok_or(Error::Denied)?;
        if handle.name != name
            || handle.generation != d.generation
            || !d.methods.contains(method)
            || !valid_path(path)
        {
            return Err(Error::Denied);
        }
        let encoded = body
            .map(crate::project::wire::encode)
            .transpose()?
            .map(|j| j.to_string());
        if encoded
            .as_ref()
            .is_some_and(|v| v.len() > crate::MAX_SOURCE_BYTES)
        {
            return Err(Error::Limit("HTTP request bytes"));
        }
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()?;
        let reply = runtime.block_on(async {
            tokio::time::timeout(
                crate::budget::remaining(Duration::from_millis(d.timeout_ms))?,
                request(d, method, path, encoded.as_deref()),
            )
            .await
            .map_err(|_| unavailable())?
        });
        let value = reply.and_then(|bytes| {
            if d.credential
                .as_ref()
                .is_some_and(|secret| bytes.windows(secret.len()).any(|w| w == secret.as_bytes()))
            {
                return Err(unavailable());
            }
            let json: serde_json::Value =
                crate::project::wire::strict_from_slice(&bytes).map_err(|_| unavailable())?;
            if d.credential
                .as_ref()
                .is_some_and(|secret| contains_credential(&json, secret))
            {
                return Err(unavailable());
            }
            crate::project::wire::decode(result, &json, program).map_err(|_| unavailable())
        });
        if method == "POST" {
            // After attempting an external write, loss of delivery cannot establish rollback.
            value.map_err(|_| {
                Error::OutcomeUnknown(crate::authority::random_hex().unwrap_or_default())
            })
        } else {
            value
        }
    }
}

fn validate_native_grants(config: &Configuration) -> Result<()> {
    let invalid_git = config.git.len() > 16
        || config.git.iter().any(|(name, grant)| {
            !crate::project::resolve::identifier(name)
                || !grant.root.is_absolute()
                || !grant.executable.is_absolute()
        });
    let invalid_delivery = config.delivery.len() > 16
        || config.delivery.iter().any(|(name, grant)| {
            !crate::project::resolve::identifier(name)
                || !grant.root.is_absolute()
                || !grant.sandbox.is_absolute()
                || !grant.limit.is_absolute()
        });
    let invalid_operations = config.operations.len() > 16
        || config.operations.iter().any(|(name, grant)| {
            !crate::project::resolve::identifier(name) || !grant.root.is_absolute()
        });
    let invalid_mail = config.mail.len() > 16
        || config.mail.iter().any(|(name, grant)| {
            !crate::project::resolve::identifier(name)
                || !grant.root.is_absolute()
                || crate::project::mail::address(&grant.from).is_err()
        })
        || config
            .mail
            .values()
            .map(|grant| &grant.root)
            .collect::<BTreeSet<_>>()
            .len()
            != config.mail.len();
    if invalid_git || invalid_delivery || invalid_operations || invalid_mail {
        Err(invalid())
    } else {
        Ok(())
    }
}

impl OAuthRuntime {
    fn capture(configuration: OAuthConfiguration) -> Result<Self> {
        use sha2::Digest as _;
        if !valid_environment_name(&configuration.transaction_key_env)
            || configuration.google.is_none() && configuration.github.is_none()
        {
            return Err(invalid());
        }
        let key_source = Zeroizing::new(
            std::env::var(&configuration.transaction_key_env).map_err(|_| invalid())?,
        );
        if !(32..=4096).contains(&key_source.len()) || key_source.chars().any(char::is_control) {
            return Err(invalid());
        }
        let key: [u8; 32] = sha2::Sha256::digest(key_source.as_bytes()).into();
        Ok(Self {
            transaction_key: Zeroizing::new(key),
            google: configuration.google.map(OAuthClient::capture).transpose()?,
            github: configuration.github.map(OAuthClient::capture).transpose()?,
        })
    }

    pub(crate) fn client(&self, provider: &str) -> Option<&OAuthClient> {
        match provider {
            "google" => self.google.as_ref(),
            "github" => self.github.as_ref(),
            _ => None,
        }
    }
}

impl OAuthClient {
    fn capture(configuration: OAuthClientConfiguration) -> Result<Self> {
        if configuration.client_id.is_empty()
            || configuration.client_id.len() > 256
            || configuration.client_id.chars().any(char::is_control)
            || !valid_environment_name(&configuration.client_secret_env)
        {
            return Err(invalid());
        }
        let secret =
            Zeroizing::new(std::env::var(&configuration.client_secret_env).map_err(|_| invalid())?);
        if secret.is_empty() || secret.len() > 4096 || secret.chars().any(char::is_control) {
            return Err(invalid());
        }
        Ok(Self {
            client_id: configuration.client_id,
            client_secret: secret,
        })
    }
}

fn valid_environment_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}
#[derive(Clone, PartialEq, Eq)]
pub(crate) struct SecretHandle {
    pub name: String,
    generation: String,
}
impl fmt::Debug for SecretHandle {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("Secret([PRIVATE])")
    }
}
fn invalid() -> Error {
    Error::Input("invalid native runtime configuration".into())
}
fn unavailable() -> Error {
    Error::Propagation("External request failed.".into())
}
fn valid_path(path: &str) -> bool {
    path.len() <= 4096
        && path.starts_with('/')
        && !path.starts_with("//")
        && !path.contains(['\\', '#', '%'])
        && !path.chars().any(char::is_control)
        && !path
            .split('?')
            .next()
            .unwrap_or_default()
            .split('/')
            .any(|s| s == "." || s == "..")
}
fn permitted(ip: IpAddr, network: Network) -> bool {
    if matches!(network, Network::Loopback) {
        return ip.is_loopback();
    }
    match ip {
        IpAddr::V4(v) => {
            let [a, b, c, _] = v.octets();
            !v.is_private()
                && !v.is_loopback()
                && !v.is_link_local()
                && !v.is_broadcast()
                && a != 0
                && a < 224
                && !(a == 100 && (64..=127).contains(&b))
                && !(a == 192 && (b == 0 || (b == 88 && c == 99)))
                && !(a == 198 && (b == 18 || b == 19 || (b == 51 && c == 100)))
                && !(a == 203 && b == 0 && c == 113)
        }
        IpAddr::V6(v) => {
            let s = v.segments();
            s[0] & 0xe000 == 0x2000
                && s[0] < 0x3ffe
                && !(s[0] == 0x2001 && (s[1] < 0x200 || s[1] == 0xdb8))
                && s[0] != 0x2002
        }
    }
}
async fn request(d: &Destination, method: &str, path: &str, body: Option<&str>) -> Result<Vec<u8>> {
    let mut target = url::Url::parse(&format!("{}{path}", d.origin)).map_err(|_| Error::Denied)?;
    for redirects in 0..=d.max_redirects {
        if target.origin().ascii_serialization() != d.origin
            || !valid_path(target.path())
            || target.as_str().len() > 8192
            || !d.paths.iter().any(|p| {
                target.path() == p
                    || target
                        .path()
                        .strip_prefix(p)
                        .is_some_and(|rest| p.ends_with('/') || rest.starts_with('/'))
            })
        {
            return Err(Error::Denied);
        }
        let host = target.host_str().ok_or(Error::Denied)?;
        let port = target.port_or_known_default().ok_or(Error::Denied)?;
        let addresses = resolve(d, host, port).await?;
        if addresses.is_empty()
            || addresses.len() > 32
            || addresses.iter().any(|a| !permitted(a.ip(), d.network))
        {
            return Err(Error::Denied);
        }
        let client = client(d, host, &addresses)?;
        let mut call = client
            .request(
                if method == "GET" {
                    reqwest::Method::GET
                } else {
                    reqwest::Method::POST
                },
                target.clone(),
            )
            .header("accept", "application/json")
            .header("accept-encoding", "identity");
        if let Some(secret) = &d.credential {
            call = call.bearer_auth(secret.as_str());
        }
        if let Some(body) = body {
            call = call
                .header("content-type", "application/json")
                .body(body.to_owned());
        }
        let response = call.send().await.map_err(|_| unavailable())?;
        if !response
            .remote_addr()
            .is_some_and(|a| addresses.contains(&a))
        {
            return Err(Error::Denied);
        }
        if response.status().is_redirection() {
            if method != "GET" || redirects == d.max_redirects {
                return Err(Error::Denied);
            }
            let location = response
                .headers()
                .get("location")
                .and_then(|v| v.to_str().ok())
                .ok_or(Error::Denied)?;
            target = target.join(location).map_err(|_| Error::Denied)?;
            if !target.username().is_empty()
                || target.password().is_some()
                || target.fragment().is_some()
            {
                return Err(Error::Denied);
            }
            continue;
        }
        if !response.status().is_success()
            || response
                .headers()
                .get("content-encoding")
                .is_some_and(|v| v != "identity")
            || response
                .headers()
                .get("content-type")
                .and_then(|v| v.to_str().ok())
                .is_none_or(|v| v.split(';').next() != Some("application/json"))
            || response
                .content_length()
                .is_some_and(|n| n > d.max_response_bytes as u64)
        {
            return Err(unavailable());
        }
        return read_response(response, d.max_response_bytes).await;
    }
    Err(Error::Denied)
}

pub(crate) fn contracts() -> serde_json::Value {
    serde_json::json!({
        "target":"server", "capability":"Http<static destination>",
        "operations":{"secret":"(static name) -> Secret<name>","http_get":"<Reply>(static name, Text, Secret<name>) -> Result<Reply, DataError>","http_post":"<Request, Reply>(static name, Text, Secret<name>, Request) -> Result<Reply, DataError>"},
        "maximum_input_bytes":1_048_576,"maximum_output_bytes":1_048_576,"maximum_deadline_ms":5000,
        "transaction":"no explicit transaction or application writes; read-only authorization snapshot permitted",
        "replay":"never automatic; failed POST delivery is outcome_unknown",
        "cancellation":"deadline drops owned HTTP futures, clients, sockets and runtime; external side effects cannot be rolled back",
        "failure":"redacted typed GET failure; conservative unknown POST outcome; static/runtime grant denial",
        "authority":"native configuration only; secrets cannot be displayed, compared, stored or transported"
    })
}

fn contains_credential(json: &serde_json::Value, credential: &str) -> bool {
    match json {
        serde_json::Value::String(s) => s.contains(credential),
        serde_json::Value::Array(values) => {
            values.iter().any(|v| contains_credential(v, credential))
        }
        serde_json::Value::Object(values) => values
            .iter()
            .any(|(k, v)| k.contains(credential) || contains_credential(v, credential)),
        _ => false,
    }
}

#[derive(Clone, Copy)]
pub(crate) struct Call<'a> {
    pub method: &'a str,
    pub name: &'a str,
    pub path: &'a str,
    pub handle: &'a SecretHandle,
    pub body: Option<&'a Value>,
}

fn client(d: &Destination, host: &str, addresses: &[SocketAddr]) -> Result<reqwest::Client> {
    let mut builder = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .referer(false)
        .http1_only()
        .pool_max_idle_per_host(0)
        .no_gzip()
        .no_brotli()
        .no_deflate()
        .no_zstd()
        .timeout(Duration::from_millis(d.timeout_ms))
        .connect_timeout(Duration::from_millis(d.timeout_ms))
        .resolve_to_addrs(host, addresses);
    if let Some(certificate) = &d.certificate {
        builder = builder.add_root_certificate(certificate.clone());
    }
    builder.build().map_err(|_| unavailable())
}

async fn read_response(response: reqwest::Response, limit: usize) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    let mut stream = response.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| unavailable())?;
        if bytes.len().saturating_add(chunk.len()) > limit {
            return Err(unavailable());
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

struct DnsConfiguration {
    config: hickory_resolver::config::ResolverConfig,
    options: hickory_resolver::config::ResolverOpts,
    hosts: std::sync::Arc<hickory_resolver::Hosts>,
}
impl DnsConfiguration {
    fn capture(destination: &Destination) -> Result<Self> {
        use hickory_resolver::config::{
            LookupIpStrategy, NameServerConfig, ResolveHosts, ResolverConfig, ResolverOpts,
        };
        let (config, mut options) = if let Some(server) = destination.dns_server {
            if !matches!(destination.network, Network::Loopback)
                || !server.ip().is_loopback()
                || server.port() == 0
            {
                return Err(invalid());
            }
            let mut nameserver = NameServerConfig::udp(server.ip());
            nameserver.connections[0].port = server.port();
            (
                ResolverConfig::from_name_servers(vec![nameserver]),
                ResolverOpts::default(),
            )
        } else {
            hickory_resolver::system_conf::read_system_conf().map_err(|_| invalid())?
        };
        options.ip_strategy = LookupIpStrategy::Ipv4AndIpv6;
        options.cache_size = 0;
        options.attempts = 1;
        options.timeout = Duration::from_millis(destination.timeout_ms);
        options.use_hosts_file = ResolveHosts::Never;
        let hosts = if destination.dns_server.is_some() {
            hickory_resolver::Hosts::default()
        } else {
            hickory_resolver::Hosts::from_system().map_err(|_| invalid())?
        };
        Ok(Self {
            config,
            options,
            hosts: std::sync::Arc::new(hosts),
        })
    }
}
async fn resolve(destination: &Destination, host: &str, port: u16) -> Result<Vec<SocketAddr>> {
    let host = host.trim_matches(['[', ']']);
    if let Ok(ip) = host.parse::<IpAddr>() {
        return Ok(vec![SocketAddr::new(ip, port)]);
    }
    let dns = destination.dns.as_ref().ok_or(Error::Denied)?;
    let mut resolver = hickory_resolver::TokioResolver::builder_with_config(
        dns.config.clone(),
        hickory_resolver::net::runtime::TokioRuntimeProvider::default(),
    )
    .with_options(dns.options.clone())
    .build()
    .map_err(|_| unavailable())?;
    resolver.set_hosts(std::sync::Arc::clone(&dns.hosts));
    // Both lookup and its DNS socket tasks live on the call's owned runtime.
    // Its deadline can drop async DNS without joining an uncancelable getaddrinfo.
    let lookup = resolver.lookup_ip(host).await.map_err(|_| unavailable())?;
    Ok(lookup.iter().map(|ip| SocketAddr::new(ip, port)).collect())
}
