#ifndef ENGINE_CLIENT_SLEEP_H
#define ENGINE_CLIENT_SLEEP_H

#include <engine/console.h>

class CSleepMode
{
public:
	class IClient *m_pClient;
	IConsole *m_pConsole;
	bool m_IsSleeping;

	CSleepMode();
	void OnInit();

private:
	static void ConSleep(IConsole::IResult *pResult, void *pUserData);
	static void ConWake(IConsole::IResult *pResult, void *pUserData);
};

#endif