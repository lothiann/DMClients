#ifndef ENGINE_CLIENT_BRIDGE_H
#define ENGINE_CLIENT_BRIDGE_H

#include <base/vmath.h>

#include <string>

class IConsole;
class IGameClient;
class IClient;

class CBridge
{
public:
	unsigned long long m_Socket;
	unsigned long long m_SendSocket;
	bool m_SendConnected;

private:
	bool m_Connected;

	IGameClient *m_pGameClient;
	IClient *m_pClient;
	IConsole *m_pConsole;

	int64_t m_LastSendTime;
	int64_t m_LastReconnectAttempt;
	std::string m_CommandBuffer;
	std::string m_Token;

	void SendGameState();
	bool TryConnectControl();
	bool TryConnectBridge();

public:
	CBridge();
	~CBridge();

	void Init(IGameClient *pGameClient, IClient *pClient, IConsole *pConsole);
	void Update(IConsole *pConsole);
};

#endif