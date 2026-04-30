#ifndef ENGINE_CLIENT_BRIDGE_H
#define ENGINE_CLIENT_BRIDGE_H

#include <base/vmath.h>
#include <string> // Нужно для std::string

class IConsole;
class IGameClient;
class IClient;

class CBridge
{
	unsigned long long m_Socket;
	unsigned long long m_SendSocket;
	bool m_Connected;
	bool m_SendConnected;

	IGameClient *m_pGameClient;
	IClient *m_pClient;

	int64_t m_LastSendTime;       // Таймер для ограничения частоты отправки
	std::string m_CommandBuffer;  // Буфер для склейки TCP-пакетов по \n

	void SendGameState();

public:
	CBridge();
	~CBridge();

	void Init(IGameClient *pGameClient, IClient *pClient);
	void Update(IConsole *pConsole);
};

#endif