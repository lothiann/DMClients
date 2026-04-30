#ifndef ENGINE_CLIENT_BOTNET_H
#define ENGINE_CLIENT_BOTNET_H

#include <engine/console.h>
#include <base/vmath.h>

class IClient;
class CBotControl;
class IGameClient;
class CGameClient;

class CBotNet
{
public:
	IClient *m_pClient;
	CBotControl *m_pBotControl;
	IConsole *m_pConsole;
	CGameClient *m_pGameChild;

	bool m_RandomAim;
	int m_RandomAimInterval;
	int64_t m_NextRandomAimTick;

	bool m_CopyMoves;
	int m_CopyTargetID;
	int m_LastTargetAttackTick;
	int m_JumpTicks;          // однократный прыжок

	bool m_AttackEnabled;
	int m_MainID;
	bool m_AllTarget; 
	bool m_TargetList[64];   
	bool m_BotsList[64]; 

	bool m_AutoAim;
	bool m_AutoFire;
	bool m_AutoHook;
	bool m_MoveEnabled;
	bool m_StandEnabled;
	bool m_RescueFrozen;
	bool m_RescueAll;
	bool m_KillOnFreeze;
	bool m_AttackMain;
	bool m_AutoHammer;

	// Новые поля
	int  m_ClientDelay;       // задержка действий в мс (0 – отключена)
	bool m_StandOnXOnly;      // останавливаться только по горизонтали

	float m_FireDist;
	float m_HookDist;
	float m_RescueRadius;
	float m_TargetDist;

	int m_HookDelay;
	int m_HookTickTimer;

	CBotNet();
	void Init(IClient *pClient, CBotControl *pBotControl, IConsole *pConsole, IGameClient *pGameClient);
	void OnTick();

	static void ConRandomAim(IConsole::IResult *pResult, void *pUserData);
	static void ConCopyMoves(IConsole::IResult *pResult, void *pUserData);
	static void ConAttackEnable(IConsole::IResult *pResult, void *pUserData);
	static void ConSetMain(IConsole::IResult *pResult, void *pUserData);
	static void ConSetTargets(IConsole::IResult *pResult, void *pUserData);
	static void ConSetBots(IConsole::IResult *pResult, void *pUserData);
	static void ConSetTargetAll(IConsole::IResult *pResult, void *pUserData);
	static void ConAttackSettings(IConsole::IResult *pResult, void *pUserData);
	static void ConAttackDists(IConsole::IResult *pResult, void *pUserData);
	static void ConAttackHookDelay(IConsole::IResult *pResult, void *pUserData);
	static void ConClientDelay(IConsole::IResult *pResult, void *pUserData);
	static void ConStandOnX(IConsole::IResult *pResult, void *pUserData);
};

#endif