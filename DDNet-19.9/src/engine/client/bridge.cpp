#include "bridge.h"

#include <engine/client.h>
#include <engine/console.h>

#include <game/client/gameclient.h>
#include <base/time.h>

#include <random>
#include <sstream>
#include <iomanip>

#if defined(CONF_FAMILY_WINDOWS)
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

static std::string GenerateToken()
{
	static std::random_device rd;
	static std::mt19937 gen(rd());
	static std::uniform_int_distribution<> dis(0, 15);
	std::stringstream ss;
	ss << std::hex;
	for(int i = 0; i < 16; i++)
		ss << std::setw(2) << std::setfill('0') << dis(gen);
	return ss.str();
}

static void ConSync(IConsole::IResult *pResult, void *pUserData)
{
	CBridge *pBridge = (CBridge *)pUserData;
	std::string newToken = GenerateToken();
	std::string ctrl_msg = "TOKEN " + newToken + "\n";
	send((SOCKET)pBridge->m_Socket, ctrl_msg.c_str(), (int)ctrl_msg.size(), 0);
	if(pBridge->m_SendConnected)
	{
		std::string bridge_msg = "TOKEN " + newToken + "\n";
		send((SOCKET)pBridge->m_SendSocket, bridge_msg.c_str(), (int)bridge_msg.size(), 0);
	}
}

CBridge::CBridge() :
	m_Socket((unsigned long long)-1),
	m_SendSocket((unsigned long long)-1),
	m_Connected(false),
	m_SendConnected(false),
	m_pGameClient(nullptr),
	m_pClient(nullptr),
	m_LastSendTime(0)
{
}

CBridge::~CBridge()
{
#if defined(CONF_FAMILY_WINDOWS)
	if(m_Connected)
		closesocket((SOCKET)m_Socket);
	if(m_SendConnected)
		closesocket((SOCKET)m_SendSocket);
	WSACleanup();
#else
	if(m_Connected && m_Socket != (unsigned long long)-1)
		close((int)m_Socket);
	if(m_SendConnected && m_SendSocket != (unsigned long long)-1)
		close((int)m_SendSocket);
#endif
}

void CBridge::Init(IGameClient *pGameClient, IClient *pClient, IConsole *pConsole)
{
	m_pGameClient = pGameClient;
	m_pClient = pClient;
	m_LastSendTime = time_get();

	pConsole->Register("c_sync", "", CFGFLAG_CLIENT, ConSync, this, "Sync token with control server");

	std::string token = GenerateToken();

#if defined(CONF_FAMILY_WINDOWS)
	WSADATA wsaData;
	if(WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
		return;
#endif

	int flag = 1;

#if defined(CONF_FAMILY_WINDOWS)
	SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
#else
	int s = socket(AF_INET, SOCK_STREAM, 0);
#endif
	if(s == (unsigned long long)-1)
		return;
	m_Socket = (unsigned long long)s;

	struct sockaddr_in serv_addr;
	serv_addr.sin_family = AF_INET;
	serv_addr.sin_port = htons(5555);
	serv_addr.sin_addr.s_addr = inet_addr("127.0.0.1");

	if(connect((SOCKET)m_Socket, (struct sockaddr *)&serv_addr, sizeof(serv_addr)) < 0)
	{
		m_Connected = false;
	}
	else
	{
		m_Connected = true;
		setsockopt((SOCKET)m_Socket, IPPROTO_TCP, TCP_NODELAY, (const char *)&flag, sizeof(flag));
#if defined(CONF_FAMILY_WINDOWS)
		unsigned long mode = 1;
		ioctlsocket((SOCKET)m_Socket, FIONBIO, &mode);
#else
		int flags = fcntl((int)m_Socket, F_GETFL, 0);
		fcntl((int)m_Socket, F_SETFL, flags | O_NONBLOCK);
#endif

#if defined(CONF_FAMILY_WINDOWS)
		Sleep(3500);
#endif
		std::string ctrl_msg = "TOKEN " + token + "\n";
		send((SOCKET)m_Socket, ctrl_msg.c_str(), (int)ctrl_msg.size(), 0);
	}

#if defined(CONF_FAMILY_WINDOWS)
	SOCKET send_s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
#else
	int send_s = socket(AF_INET, SOCK_STREAM, 0);
#endif
	if(send_s == (unsigned long long)-1)
	{
		dbg_msg("bridge", "failed to create send socket");
		return;
	}
	m_SendSocket = (unsigned long long)send_s;

	struct sockaddr_in send_addr;
	send_addr.sin_family = AF_INET;
	send_addr.sin_port = htons(5556);
	send_addr.sin_addr.s_addr = inet_addr("127.0.0.1");

	if(connect((SOCKET)m_SendSocket, (struct sockaddr *)&send_addr, sizeof(send_addr)) < 0)
	{
		dbg_msg("bridge", "failed to connect to send port 5556");
		m_SendConnected = false;
	}
	else
	{
		m_SendConnected = true;
		setsockopt((SOCKET)m_SendSocket, IPPROTO_TCP, TCP_NODELAY, (const char *)&flag, sizeof(flag));
		int sndbuf = 256 * 1024;
		setsockopt((SOCKET)m_SendSocket, SOL_SOCKET, SO_SNDBUF, (const char *)&sndbuf, sizeof(sndbuf));
#if defined(CONF_FAMILY_WINDOWS)
		unsigned long mode = 1;
		ioctlsocket((SOCKET)m_SendSocket, FIONBIO, &mode);
#else
		int flags = fcntl((int)m_SendSocket, F_GETFL, 0);
		fcntl((int)m_SendSocket, F_SETFL, flags | O_NONBLOCK);
#endif
		std::string bridge_msg = "TOKEN " + token + "\n";
		send((SOCKET)m_SendSocket, bridge_msg.c_str(), (int)bridge_msg.size(), 0);
	}
}

void CBridge::SendGameState()
{
	if(!m_pGameClient || !m_SendConnected || !m_pClient)
		return;

	int64_t Now = time_get();
	if(Now - m_LastSendTime < time_freq() / 150)
		return;
	m_LastSendTime = Now;

	CGameClient *pGameClient = static_cast<CGameClient *>(m_pGameClient);
	const CGameClient::CSnapState &Snap = pGameClient->m_Snap;
	if(!Snap.m_pLocalInfo)
		return;

	char aBuffer[32768];
	int Len = 0;

	CServerInfo ServerInfo;
	m_pClient->GetServerInfo(&ServerInfo);
	Len += str_format(aBuffer + Len, sizeof(aBuffer) - Len,
		"SERVER \"%s\" \"%s\" \"%s\" %d %d\n",
		ServerInfo.m_aName,
		ServerInfo.m_aMap,
		ServerInfo.m_aGameType,
		ServerInfo.m_NumPlayers,
		ServerInfo.m_MaxPlayers);

	for(int i = 0; i < MAX_CLIENTS; ++i)
	{
		const CGameClient::CClientData &Client = pGameClient->m_aClients[i];
		if(!Client.m_Active)
			continue;

		const CGameClient::CSnapState::CCharacterInfo *pChar = &Snap.m_aCharacters[i];
		vec2 Pos(0.0f, 0.0f);
		int Weapon = -1;
		int Health = 0;
		int Armor = 0;
		int Direction = 0;
		int Jumped = 0;
		int HookState = 0;
		int Angle = 0;
		int AttackTick = 0;
		int TargetX = 0, TargetY = 0;

		if(pChar->m_Active)
		{
			Pos = vec2(pChar->m_Cur.m_X, pChar->m_Cur.m_Y);
			Weapon = pChar->m_Cur.m_Weapon;
			Health = pChar->m_Cur.m_Health;
			Armor = pChar->m_Cur.m_Armor;
			Direction = pChar->m_Cur.m_Direction;
			Jumped = pChar->m_Cur.m_Jumped;
			HookState = pChar->m_Cur.m_HookState;
			Angle = pChar->m_Cur.m_Angle;
			AttackTick = pChar->m_Cur.m_AttackTick;
			if(pChar->m_HasExtendedData)
			{
				TargetX = pChar->m_ExtendedData.m_TargetX;
				TargetY = pChar->m_ExtendedData.m_TargetY;
			}
		}
		else if(Client.m_SpecCharPresent)
		{
			Pos = Client.m_SpecChar;
		}
		else
		{
			continue;
		}

		int Frozen = (Client.m_FreezeEnd > 0) ? 1 : 0;
		int Local = (i == pGameClient->m_Snap.m_LocalClientId) ? 1 : 0;

		Len += str_format(aBuffer + Len, sizeof(aBuffer) - Len,
			"PLAYER %d \"%s\" %.2f %.2f %d %d %d %d %d %d %d %d %d %d %d %d %d %d\n",
			i,
			Client.m_aName,
			Pos.x, Pos.y,
			Client.m_Team,
			Weapon,
			Health,
			Armor,
			Frozen,
			Local,
			Direction,
			Jumped,
			HookState,
			Angle,
			AttackTick,
			TargetX,
			TargetY);

		if(Len >= (int)sizeof(aBuffer) - 512)
			break;
	}

	if(Len == 0)
		return;

	int sent = send((SOCKET)m_SendSocket, aBuffer, Len, 0);
	if(sent < 0)
	{
#if defined(CONF_FAMILY_WINDOWS)
		int err = WSAGetLastError();
		if(err != WSAEWOULDBLOCK)
#else
		if(errno != EAGAIN && errno != EWOULDBLOCK)
#endif
		{
			dbg_msg("bridge", "send error, disconnecting send socket");
			m_SendConnected = false;
			closesocket((SOCKET)m_SendSocket);
		}
	}
}

void CBridge::Update(IConsole *pConsole)
{
	if(m_Connected)
	{
		char aBuf[4096];
		int nBytes = recv((SOCKET)m_Socket, aBuf, sizeof(aBuf) - 1, 0);
		if(nBytes > 0)
		{
			aBuf[nBytes] = '\0';
			m_CommandBuffer += aBuf;
			size_t pos;
			while((pos = m_CommandBuffer.find('\n')) != std::string::npos)
			{
				std::string cmd = m_CommandBuffer.substr(0, pos);
				if(!cmd.empty())
				{
					pConsole->ExecuteLine(cmd.c_str(), -1, -1);
				}
				m_CommandBuffer.erase(0, pos + 1);
			}
		}
	}

	SendGameState();
}