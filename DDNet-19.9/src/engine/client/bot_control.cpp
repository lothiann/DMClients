#include "bot_control.h"

#include <base/system.h>

#include <engine/client.h>
#include <engine/shared/config.h>

CBotControl::CBotControl()
{
	m_pClient = nullptr;
	m_pConsole = nullptr;
	ActionStop();
}

void CBotControl::OnInit()
{
	if(m_pConsole)
	{
		m_pConsole->Register("c_input", "s[name] i[ms]", CFGFLAG_CLIENT, ConInput, this, "Подать ввод боту");
		m_pConsole->Register("c_stop", "", CFGFLAG_CLIENT, ConStop, this, "Остановить бота");
		m_pConsole->Register("c_aim", "i[dx] i[dy]", CFGFLAG_CLIENT, ConAim, this, "Сместить прицел бота (относительно)");
		m_pConsole->Register("c_oaim", "i[x] i[y]", CFGFLAG_CLIENT, ConOverrideAim, this, "Установить абсолютные координаты прицела бота");
	}
}

bool CBotControl::IsBotActiveJump() { return m_pClient && m_JumpEndTick > 0 && m_pClient->GameTick(g_Config.m_ClDummy) <= m_JumpEndTick + 1; }
bool CBotControl::IsBotActiveHook() { return m_pClient && m_HookEndTick > 0 && m_pClient->GameTick(g_Config.m_ClDummy) <= m_HookEndTick + 1; }
bool CBotControl::IsBotActiveFire() { return m_pClient && m_FireEndTick > 0 && m_pClient->GameTick(g_Config.m_ClDummy) <= m_FireEndTick + 1; }
bool CBotControl::IsBotActiveMove() { return m_pClient && m_MoveEndTick > 0 && m_pClient->GameTick(g_Config.m_ClDummy) <= m_MoveEndTick + 1; }
bool CBotControl::IsBotActiveAim() { return m_pClient && m_AimEndTick > 0 && m_pClient->GameTick(g_Config.m_ClDummy) <= m_AimEndTick + 1; }
bool CBotControl::IsBotActiveOverrideAim() { return m_pClient && m_OverrideAimEndTick > 0 && m_pClient->GameTick(g_Config.m_ClDummy) <= m_OverrideAimEndTick + 1; }

void CBotControl::ActionInput(const char *pName, int MS)
{
	if(!m_pClient)
		return;

	int TickSpeed = m_pClient->GameTickSpeed();
	if(TickSpeed <= 0)
		TickSpeed = 50;

	int64_t Cur = m_pClient->GameTick(g_Config.m_ClDummy);
	int64_t EndTick = Cur + (int64_t)TickSpeed * MS / 1000;

	if(str_comp(pName, "jump") == 0)
	{
		m_Jump = true;
		m_JumpEndTick = EndTick;
	}
	else if(str_comp(pName, "hook") == 0)
	{
		m_Hook = true;
		m_HookEndTick = EndTick;
	}
	else if(str_comp(pName, "fire") == 0)
	{
		m_Fire = true;
		m_FireEndTick = EndTick;
	}
	else if(str_comp(pName, "left") == 0)
	{
		m_Direction = -1;
		m_MoveEndTick = EndTick;
	}
	else if(str_comp(pName, "right") == 0)
	{
		m_Direction = 1;
		m_MoveEndTick = EndTick;
	}
}

void CBotControl::ActionAim(int dx, int dy)
{
	if(!m_pClient)
		return;
	m_TargetX = dx;
	m_TargetY = dy;
	m_AimEndTick = m_pClient->GameTick(g_Config.m_ClDummy) + 2;
}

void CBotControl::ActionOverrideAim(int x, int y)
{
	if(!m_pClient)
		return;
	m_OverrideTargetX = x;
	m_OverrideTargetY = y;
	m_OverrideAimEndTick = m_pClient->GameTick(g_Config.m_ClDummy) + 2;
}

void CBotControl::OnTick()
{
	if(!m_pClient)
		return;
	int64_t Cur = m_pClient->GameTick(g_Config.m_ClDummy);

	if(m_JumpEndTick > 0 && Cur >= m_JumpEndTick)
		m_Jump = false;
	if(m_HookEndTick > 0 && Cur >= m_HookEndTick)
		m_Hook = false;
	if(m_FireEndTick > 0 && Cur >= m_FireEndTick)
		m_Fire = false;
	if(m_MoveEndTick > 0 && Cur >= m_MoveEndTick)
		m_Direction = 0;

	if(m_JumpEndTick > 0 && Cur > m_JumpEndTick + 2)
		m_JumpEndTick = 0;
	if(m_HookEndTick > 0 && Cur > m_HookEndTick + 2)
		m_HookEndTick = 0;
	if(m_FireEndTick > 0 && Cur > m_FireEndTick + 2)
		m_FireEndTick = 0;
	if(m_MoveEndTick > 0 && Cur > m_MoveEndTick + 2)
		m_MoveEndTick = 0;

	// Очистка относительного прицела
	if(m_AimEndTick > 0 && Cur > m_AimEndTick + 2)
	{
		m_TargetX = 0;
		m_TargetY = 0;
		m_AimEndTick = 0;
	}

	// Очистка абсолютного прицела
	if(m_OverrideAimEndTick > 0 && Cur > m_OverrideAimEndTick + 2)
	{
		m_OverrideTargetX = 0;
		m_OverrideTargetY = 0;
		m_OverrideAimEndTick = 0;
	}
}

void CBotControl::ActionStop()
{
	m_Jump = m_Hook = m_Fire = false;
	m_Direction = 0;
	m_TargetX = m_TargetY = 0;
	m_OverrideTargetX = m_OverrideTargetY = 0;
	m_JumpEndTick = m_HookEndTick = m_FireEndTick = m_MoveEndTick = m_AimEndTick = m_OverrideAimEndTick = 0;
}

void CBotControl::ConInput(IConsole::IResult *pResult, void *pUserData) { ((CBotControl *)pUserData)->ActionInput(pResult->GetString(0), pResult->GetInteger(1)); }
void CBotControl::ConStop(IConsole::IResult *pResult, void *pUserData) { ((CBotControl *)pUserData)->ActionStop(); }
void CBotControl::ConAim(IConsole::IResult *pResult, void *pUserData) { ((CBotControl *)pUserData)->ActionAim(pResult->GetInteger(0), pResult->GetInteger(1)); }
void CBotControl::ConOverrideAim(IConsole::IResult *pResult, void *pUserData) { ((CBotControl *)pUserData)->ActionOverrideAim(pResult->GetInteger(0), pResult->GetInteger(1)); }