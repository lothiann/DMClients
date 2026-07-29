/* (c) HDDNet. SOCKS5 client-side proxy for redirecting all UDP and HTTP traffic. */
#ifndef ENGINE_CLIENT_SOCKS5_PROXY_H
#define ENGINE_CLIENT_SOCKS5_PROXY_H

#include <base/net.h>
#include <base/types.h>

#include <engine/console.h>
#include <engine/shared/network.h> // NET_MAX_PACKETSIZE

/**
 * CSocks5Proxy
 *
 * Redirects ALL client traffic through a SOCKS5 proxy (e.g. local xray socks-inbound).
 *
 *  - One TCP control connection to the proxy is kept alive for the whole client lifetime.
 *  - A single UDP ASSOCIATE session is established over that control connection.
 *  - All UDP packets sent via net_udp_send are wrapped in a SOCKS5 UDP request header
 *    (RSV/FRAG/ATYP/DST.ADDR/DST.PORT + payload) and sent to the relay address returned
 *    by the proxy. Responses coming back from the relay are unwrapped: the real source
 *    address (extracted from the SOCKS5 header) is restored so the upper network layers
 *    see packets as if they came directly from the game server.
 *  - HTTP/curl traffic is routed via CURLOPT_PROXY with CURLPROXY_SOCKS5_HOSTNAME
 *    (remote DNS), see http_set_socks5_proxy().
 *
 * IMPORTANT: the entire handshake (TCP connect + SOCKS5 auth + UDP ASSOCIATE) is done
 * as a non-blocking state machine driven from Update(). No single Update() call ever
 * blocks for more than ~100ms, so calling it from the main client tick does not cause
 * frame hitches. This mirrors the approach used by CBridge (engine/client/bridge.cpp).
 *
 * Usage:
 *     CSocks5Proxy Proxy;
 *     Proxy.SetConsole(pConsole);
 *     Proxy.Enable("127.0.0.1:10801");  // starts async connect
 *     ...
 *     Proxy.Update();   // call every tick — drives handshake + keepalive + reconnect
 *     ...
 *     Proxy.Disable();
 *
 * The proxy is no-auth only (matches local xray socks-inbound with auth: noauth).
 */
class CSocks5Proxy
{
public:
	enum class EState
	{
		DISABLED = 0,
		CONNECTING, ///< non-blocking TCP connect in progress
		HANDSHAKING, ///< SOCKS5 auth + UDP ASSOCIATE state machine in progress
		READY, ///< UDP ASSOCIATE established, relay active
		BROKEN, ///< control connection lost, waiting for reconnect backoff
	};

private:
	/// Handshake sub-states (only meaningful when m_State == HANDSHAKING).
	enum class EHsState
	{
		AUTH_SEND, ///< sending VER NMETHODS METHODS
		AUTH_RECV, ///< receiving VER METHOD (2 bytes)
		ASSOC_SEND, ///< sending VER CMD UDP_ASSOCIATE ...
		ASSOC_RECV_HDR, ///< receiving VER REP RSV ATYP (4 bytes)
		ASSOC_RECV_ADDR, ///< receiving BND.ADDR + BND.PORT (variable)
	};

	EState m_State = EState::DISABLED;
	EHsState m_HsState = EHsState::AUTH_SEND;

	char m_aProxyAddrStr[256] = {0}; ///< original "host:port" string as passed to Enable()
	NETADDR m_ProxyAddr = NETADDR_ZEROED; ///< resolved proxy address (IPv4 or IPv6)

	// TCP control connection to the SOCKS5 server (kept alive while enabled).
	// Always non-blocking after the initial socket creation.
	NETSOCKET m_TcpControl = nullptr;

	// Local UDP socket used to talk to the SOCKS5 UDP relay.
	NETSOCKET m_UdpSocket = nullptr;
	NETADDR m_RelayAddr = NETADDR_ZEROED; ///< BND.ADDR:BND.PORT returned by UDP ASSOCIATE
	bool m_RelayAddrIsProxy = false; ///< true if server returned 0.0.0.0/:: (use proxy addr + BND.PORT)

	// --- Handshake scratch buffers (persist across ticks during async handshake) ---
	unsigned char m_aHsSendBuf[16] = {0};
	int m_HsSendLen = 0;
	int m_HsSendPos = 0;
	unsigned char m_aHsRecvBuf[32] = {0};
	int m_HsRecvNeed = 0;
	int m_HsRecvPos = 0;
	int64_t m_HsStartTime = 0; ///< time_get() when handshake phase began, for total timeout
	static constexpr int HANDSHAKE_TOTAL_TIMEOUT_SEC = 5; ///< abort handshake after this
	static constexpr int SELECT_TIMEOUT_MS = 100; ///< max block per Update() call

	// Reconnect backoff
	int64_t m_NextReconnectTime = 0;
	static constexpr int RECONNECT_BACKOFF_SEC = 2; // seconds

	// Keepalive: SOCKS5 has no ping, but we periodically drain the control socket
	// to detect a closed connection quickly.
	int64_t m_LastKeepaliveCheck = 0;
	static constexpr int KEEPALIVE_CHECK_INTERVAL_SEC = 1; // second

	// Scratch buffers for UDP relay send/recv (member so we can return stable pointers).
	unsigned char m_aSendBuf[NET_MAX_PACKETSIZE + 32] = {0};
	unsigned char m_aRecvBuf[NET_MAX_PACKETSIZE + 32] = {0};

	IConsole *m_pConsole = nullptr;

	// --- Internal helpers ---

	void Log(int Level, const char *pMsg) const;

	// Address parsing: accepts "IP:PORT", "[IPv6]:PORT", "host:PORT".
	bool ParseProxyAddr(const char *pStr);

	// Kick off a (re)connection attempt: creates a non-blocking TCP socket and
	// starts connect(). Sets m_State = CONNECTING. Returns false only if the
	// socket could not be created at all (the connect itself is async).
	bool StartConnect();

	// Drive one step of the async handshake. Called from Update() when
	// m_State == HANDSHAKING. Advances m_HsState; on completion transitions
	// to READY and creates the UDP relay socket. On error transitions to BROKEN.
	void UpdateHandshake();

	// Prepare the AUTH_SEND phase buffers.
	void PrepareAuthSend();

	// Prepare the ASSOC_SEND phase buffers.
	void PrepareAssocSend();

	// Process the bytes accumulated in m_aHsRecvBuf for the AUTH phase.
	// Returns true on success (advances m_HsState), false on protocol error.
	bool ProcessAuthReply();

	// Process the header (4 bytes) of the UDP ASSOCIATE reply. Sets m_HsRecvNeed
	// for the remaining address + port bytes. Returns false on protocol error.
	bool ProcessAssocReplyHdr();

	// Process the full UDP ASSOCIATE reply (header + addr + port). Fills
	// m_RelayAddr. Returns false on protocol error.
	bool ProcessAssocReplyFull();

	// Build a SOCKS5 UDP request header in pOut for destination pDst.
	// Returns header length or 0 on error.
	int BuildUdpHeader(const NETADDR *pDst, unsigned char *pOut, int OutMax) const;

	// Parse a SOCKS5 UDP response header. On success sets *pSrc to the real
	// source address and *pHeaderLen to the bytes consumed.
	bool ParseUdpHeader(const unsigned char *pData, int Size, NETADDR *pSrc, int *pHeaderLen) const;

	// Close TCP control and UDP relay sockets. Does not change m_State.
	void CloseSockets();

	// Transition to BROKEN state and schedule a reconnect.
	void Fail(const char *pReason);

public:
	CSocks5Proxy();
	~CSocks5Proxy();

	CSocks5Proxy(const CSocks5Proxy &) = delete;
	CSocks5Proxy &operator=(const CSocks5Proxy &) = delete;

	void SetConsole(IConsole *pConsole) { m_pConsole = pConsole; }

	/**
	 * Enable the proxy. Parses addr (host:port), starts an async TCP connect +
	 * SOCKS5 handshake, and installs global UDP/HTTP hooks immediately (so
	 * packets are buffered / dropped until READY, rather than leaking direct).
	 * Returns true if the connect was started (check IsActive() later).
	 */
	bool Enable(const char *pAddrStr);

	/**
	 * Disable the proxy: removes hooks, closes all sockets. Safe to call when
	 * already disabled.
	 */
	void Disable();

	/**
	 * Periodic update. Call every tick. Drives the async handshake, keepalive
	 * probing of the TCP control connection, and automatic reconnect with
	 * backoff. Never blocks for more than ~SELECT_TIMEOUT_MS.
	 */
	void Update();

	bool IsActive() const { return m_State == EState::READY; }
	EState State() const { return m_State; }
	const char *ProxyAddrStr() const { return m_aProxyAddrStr; }

	// --- UDP relay hooks (called from net.cpp via net_set_socks5_hooks) ---
	// Returns bytes of payload sent, or -1 on error.
	int UdpSend(const NETADDR *pDst, const void *pData, int Size);
	// Returns bytes of payload received, fills *pSrc with real source address
	// and *ppData with a pointer to a stable buffer. Returns 0 if no data.
	int UdpRecv(NETADDR *pSrc, unsigned char **ppData);

	// Singleton pointer used by the C-linkage trampolines installed into
	// net.cpp. There is only one CSocks5Proxy per client.
	static CSocks5Proxy *ActiveInstance;
};

// (net.cpp hook API and SOCKS5_UDP_SEND_HOOK / SOCKS5_UDP_RECV_HOOK typedefs
// live in base/net.h.)

// HTTP/curl proxy hook implemented in engine/shared/http.cpp.
// Pass nullptr to disable. Addr format: "host:port" (curl SOCKS5).
void http_set_socks5_proxy(const char *pAddr);

#endif // ENGINE_CLIENT_SOCKS5_PROXY_H
