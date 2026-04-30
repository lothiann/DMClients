#include "sleep.h"

#include <engine/shared/config.h>

CSleepMode::CSleepMode()
{
	m_IsSleeping = false;
	m_pClient = nullptr;
	m_pConsole = nullptr;
}

void CSleepMode::OnInit()
{
	if(m_pConsole)
	{
		m_pConsole->Register("c_sleep", "", CFGFLAG_CLIENT, ConSleep, this, "Выключить рендеринг");
		m_pConsole->Register("c_wake", "", CFGFLAG_CLIENT, ConWake, this, "Включить рендеринг");
	}
}

void CSleepMode::ConSleep(IConsole::IResult *pResult, void *pUserData)
{
	((CSleepMode *)pUserData)->m_IsSleeping = true;
}

void CSleepMode::ConWake(IConsole::IResult *pResult, void *pUserData)
{
	((CSleepMode *)pUserData)->m_IsSleeping = false;
}