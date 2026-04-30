#include "botnet.h"
#include "bot_control.h"
#include <engine/client.h>
#include <game/client/gameclient.h>
#include <base/math.h>
#include <base/system.h>
#include <stdlib.h>
#include <string.h>

CBotNet::CBotNet()
{
	m_pClient = nullptr; m_pBotControl = nullptr; m_pConsole = nullptr; m_pGameChild = nullptr;
	m_RandomAim = m_CopyMoves = m_AttackEnabled = false;
	m_MainID = -1;
	m_RandomAimInterval = 100;
	m_NextRandomAimTick = 0;
	m_LastTargetAttackTick = -1;
	m_AllTarget = false;
	m_AutoAim = m_AutoFire = m_MoveEnabled = true;
	m_AutoHook = m_StandEnabled = m_RescueFrozen = m_RescueAll = m_KillOnFreeze = m_AttackMain = m_AutoHammer = false;
	m_FireDist = 80.0f; m_HookDist = 400.0f; m_RescueRadius = 500.0f; m_TargetDist = 300.0f;
	m_HookDelay = 1000; m_HookTickTimer = 0;
	m_JumpTicks = 0;
	m_ClientDelay = 0;
	m_StandOnXOnly = false;
	for(int i=0; i<64; i++) { m_TargetList[i] = false; m_BotsList[i] = false; }
}

void CBotNet::Init(IClient *pClient, CBotControl *pBotControl, IConsole *pConsole, IGameClient *pGameClient)
{
	m_pClient = pClient; m_pBotControl = pBotControl; m_pConsole = pConsole;
	m_pGameChild = (CGameClient *)pGameClient;

	m_pConsole->Register("c_random_aim", "i[on] ?i[ms]", CFGFLAG_CLIENT, ConRandomAim, this, "Random Aim");
	m_pConsole->Register("c_copy_moves", "i[target_id]", CFGFLAG_CLIENT, ConCopyMoves, this, "Copy Moves");
	m_pConsole->Register("c_attack", "i[on]", CFGFLAG_CLIENT, ConAttackEnable, this, "Attack Mode");
	m_pConsole->Register("c_main", "i[id]", CFGFLAG_CLIENT, ConSetMain, this, "Main ID");
	m_pConsole->Register("c_targets", "s[ids]", CFGFLAG_CLIENT, ConSetTargets, this, "Targets");
	m_pConsole->Register("c_bots", "s[ids]", CFGFLAG_CLIENT, ConSetBots, this, "Bots (allies)");
	m_pConsole->Register("c_target_all", "i[on]", CFGFLAG_CLIENT, ConSetTargetAll, this, "Attack all (targets become blacklist)");
	m_pConsole->Register("c_atk_set", "iiiiiiiiii", CFGFLAG_CLIENT, ConAttackSettings, this, "Settings (10 params)");
	m_pConsole->Register("c_atk_dists", "ffff", CFGFLAG_CLIENT, ConAttackDists, this, "Radii (fire, hook, rescue, target)");
	m_pConsole->Register("c_atk_hook_delay", "i[ms]", CFGFLAG_CLIENT, ConAttackHookDelay, this, "Hook Delay");
	m_pConsole->Register("c_client_delay", "i[ms]", CFGFLAG_CLIENT, ConClientDelay, this, "Client delay in ms (0 to disable)");
	m_pConsole->Register("c_stand_on_x", "i[on]", CFGFLAG_CLIENT, ConStandOnX, this, "Stand only on X axis");
}

void CBotNet::OnTick()
{
	if(!m_pClient || !m_pBotControl || !m_pConsole || !m_pGameChild) return;
	if(m_pClient->State() != 3 && m_pClient->State() != 5) return;

	// Задержка действий: если задана, бот ждёт нужное число игровых тиков
	if(m_ClientDelay > 0)
	{
		static int64_t s_LastActionTick = 0;
		int64_t Now = m_pClient->GameTick(0);
		int64_t TickDelay = ((int64_t)m_ClientDelay * m_pClient->GameTickSpeed()) / 1000;
		if(TickDelay < 1) TickDelay = 1;
		if(Now - s_LastActionTick < TickDelay)
			return;
		s_LastActionTick = Now;
	}

	CGameClient *pGame = m_pGameChild;
	if(!pGame->m_Snap.m_pLocalInfo) return;

	int LocalID = pGame->m_Snap.m_LocalClientId;
	if(LocalID < 0 || LocalID >= 64) return;
	if(!pGame->m_aClients[LocalID].m_Active) return;

	int Dummy = g_Config.m_ClDummy;
	int64_t CurTick = m_pClient->GameTick(0);
	auto &Controls = pGame->m_Controls;
	auto &InputData = Controls.m_aInputData[Dummy];

	// --- 1. KILL ON FREEZE ---
	if(m_KillOnFreeze && pGame->m_aClients[LocalID].m_FreezeEnd > 0) {
		m_pConsole->ExecuteLine("kill", -1, -1);
	}

	// --- 2. COPY MOVES ---
	if(m_CopyMoves && m_CopyTargetID >= 0 && m_CopyTargetID < 64) {
		const auto &TChar = pGame->m_Snap.m_aCharacters[m_CopyTargetID];
		if(pGame->m_aClients[m_CopyTargetID].m_Active && TChar.m_Active) {
			Controls.m_aInputDirectionLeft[Dummy] = (TChar.m_Cur.m_Direction == -1);
			Controls.m_aInputDirectionRight[Dummy] = (TChar.m_Cur.m_Direction == 1);
			InputData.m_Jump = (TChar.m_Cur.m_Jumped & 1);
			InputData.m_Hook = (TChar.m_Cur.m_HookState > 0);
			InputData.m_Fire = TChar.m_Cur.m_AttackTick;
			InputData.m_WantedWeapon = TChar.m_Cur.m_Weapon + 1;
			float WorldX = (float)(TChar.m_HasExtendedData ? TChar.m_ExtendedData.m_TargetX : 0);
			float WorldY = (float)(TChar.m_HasExtendedData ? TChar.m_ExtendedData.m_TargetY : 0);
			m_pBotControl->ActionOverrideAim((int)WorldX, (int)WorldY);

			InputData.m_PlayerFlags |= 1;
			return;
		}
	}

	// --- 3. ATTACK/FOLLOW SYSTEM ---
	if(m_AttackEnabled) {
		int TargetID = -1;
		float MinDist = 1000000.0f;
		vec2 MyPos((float)pGame->m_Snap.m_aCharacters[LocalID].m_Cur.m_X, (float)pGame->m_Snap.m_aCharacters[LocalID].m_Cur.m_Y);
		bool TargetIsMain = false;

		// А. Спасение замороженных
		if(m_RescueFrozen) {
			for(int i = 0; i < 64; i++) {
				if(i == LocalID || !pGame->m_aClients[i].m_Active) continue;
				if(pGame->m_aClients[i].m_FreezeEnd == 0) continue;

				bool canRescue = false;
				if(i == m_MainID)
					canRescue = true;
				else if(m_BotsList[i])
					canRescue = true;
				else if(m_RescueAll) {
					if(m_AllTarget)
						canRescue = m_TargetList[i];
					else
						canRescue = !m_TargetList[i];
				}

				if(!canRescue) continue;

				vec2 TPos((float)pGame->m_Snap.m_aCharacters[i].m_Cur.m_X,
				          (float)pGame->m_Snap.m_aCharacters[i].m_Cur.m_Y);
				if(pGame->Collision()->IntersectLine(MyPos, TPos, NULL, NULL) > 0) continue;
				float d = distance(MyPos, TPos);
				if(d < m_RescueRadius && d < MinDist) {
					MinDist = d;
					TargetID = i;
				}
			}
		}

		// Б. Поиск врага
		if(TargetID == -1) {
			for(int i = 0; i < 64; i++) {
				if(i == LocalID || i == m_MainID || !pGame->m_aClients[i].m_Active) continue;
				if(m_BotsList[i]) continue;
				if(pGame->m_aClients[i].m_FreezeEnd != 0) continue;

				bool isEnemy = (m_AllTarget ? !m_TargetList[i] : m_TargetList[i]);
				if(!isEnemy) continue;

				vec2 TPos((float)pGame->m_Snap.m_aCharacters[i].m_Cur.m_X,
				          (float)pGame->m_Snap.m_aCharacters[i].m_Cur.m_Y);
				if(pGame->Collision()->IntersectLine(MyPos, TPos, NULL, NULL) > 0) continue;
				float d = distance(MyPos, TPos);

				if(m_TargetDist > 0 && d > m_TargetDist) continue;

				if(d < MinDist) {
					MinDist = d;
					TargetID = i;
				}
			}
		}

		// В. Если врагов нет, идём к мейну
		if(TargetID == -1 && m_MainID >= 0 && m_MainID < 64) {
			if(pGame->m_aClients[m_MainID].m_Active) {
				TargetID = m_MainID;
				TargetIsMain = true;
			}
		}

		// Г. Исполнение
		if(TargetID != -1) {
			auto &TChar = pGame->m_Snap.m_aCharacters[TargetID].m_Cur;
			float dx = (float)TChar.m_X - MyPos.x;
			float dy = (float)TChar.m_Y - MyPos.y;
			float Dist = distance(MyPos, vec2((float)TChar.m_X, (float)TChar.m_Y));

			if(m_AutoAim) Controls.m_aMousePos[Dummy] = vec2(dx, dy);

			if(m_MoveEnabled) {
				bool left = false, right = false;
				if(!m_StandEnabled || (!m_StandOnXOnly && Dist >= 64.0f) || (m_StandOnXOnly && absolute(dx) >= 64.0f))
				{
					left  = (dx < -20.0f);
					right = (dx > 20.0f);
				}

				Controls.m_aInputDirectionLeft[Dummy]  = left;
				Controls.m_aInputDirectionRight[Dummy] = right;

				vec2 IntersectPos;
				int HitPlayer = pGame->IntersectCharacter(MyPos, MyPos + vec2((dx > 0 ? 1.0f : -1.0f) * 40.0f, 0), IntersectPos, LocalID);

				if(pGame->Collision()->CheckPoint(vec2(MyPos.x + (dx > 0 ? 35 : -35), MyPos.y)) ||
					(dy < -60.0f && absolute(dx) < 128.0f) ||
					(HitPlayer != -1 && HitPlayer != m_MainID))
				{
					if(m_JumpTicks == 0)
					{
						InputData.m_Jump = 1;
						m_JumpTicks = 1;
					}
					else
					{
						InputData.m_Jump = 0;
						m_JumpTicks = 0;
					}
				}
				else
				{
					InputData.m_Jump = 0;
					m_JumpTicks = 0;
				}
			}

			bool CanShoot = !TargetIsMain || m_AttackMain;
			if(m_AutoFire && Dist < m_FireDist && CanShoot) InputData.m_Fire++;
			if(m_AutoHammer && InputData.m_Fire > 0) InputData.m_WantedWeapon = 1;

			if(m_AutoHook && Dist < m_HookDist && CanShoot) {
				int TicksCycle = (50 * m_HookDelay / 1000);
				if(TicksCycle < 2) TicksCycle = 2;
				m_HookTickTimer++;
				if(m_HookTickTimer >= TicksCycle) m_HookTickTimer = 0;
				InputData.m_Hook = (m_HookTickTimer < TicksCycle - 2);
			} else {
				InputData.m_Hook = 0; m_HookTickTimer = 0;
			}
			InputData.m_PlayerFlags |= 1;
		} else {
			pGame->m_Controls.m_aInputDirectionLeft[Dummy] = pGame->m_Controls.m_aInputDirectionRight[Dummy] = 0;
			InputData.m_Hook = 0; m_HookTickTimer = 0;
		}
	}

	// --- 4. RANDOM AIM ---
	if(m_RandomAim && !m_AttackEnabled && !m_CopyMoves) {
		if(CurTick >= m_NextRandomAimTick) {
			m_pBotControl->ActionOverrideAim((rand() % 2001) - 1000, (rand() % 2001) - 1000);
			int TS = m_pClient->GameTickSpeed();
			m_NextRandomAimTick = CurTick + (int64_t)(TS ? TS : 50) * m_RandomAimInterval / 1000;
		}
	}
}

// --- Консольные коллбэки ---

void CBotNet::ConAttackHookDelay(IConsole::IResult *pResult, void *pUserData) {
	((CBotNet*)pUserData)->m_HookDelay = pResult->GetInteger(0);
	((CBotNet*)pUserData)->m_HookTickTimer = 0;
}

void CBotNet::ConAttackDists(IConsole::IResult *pResult, void *pUserData) {
	CBotNet *p = (CBotNet *)pUserData;
	p->m_FireDist = pResult->GetFloat(0);
	p->m_HookDist = pResult->GetFloat(1);
	p->m_RescueRadius = pResult->GetFloat(2);
	if(pResult->NumArguments() > 3)
		p->m_TargetDist = pResult->GetFloat(3);
}

void CBotNet::ConSetTargets(IConsole::IResult *pResult, void *pUserData) {
	CBotNet *p = (CBotNet *)pUserData;
	for(int i=0; i<64; i++) p->m_TargetList[i] = false;
	const char *pL = pResult->GetString(0);
	if(!pL || !pL[0]) return;
	char aB[256]; str_copy(aB, pL, sizeof(aB));
	char *pC = aB;
	while(pC) {
		int id = atoi(pC);
		if(id >= 0 && id < 64) p->m_TargetList[id] = true;
		pC = strchr(pC, ',');
		if(pC) pC++;
	}
}

void CBotNet::ConSetBots(IConsole::IResult *pResult, void *pUserData) {
	CBotNet *p = (CBotNet *)pUserData;
	for(int i=0; i<64; i++) p->m_BotsList[i] = false;
	const char *pL = pResult->GetString(0);
	if(!pL || !pL[0]) return;
	char aB[256]; str_copy(aB, pL, sizeof(aB));
	char *pC = aB;
	while(pC) {
		int id = atoi(pC);
		if(id >= 0 && id < 64) p->m_BotsList[id] = true;
		pC = strchr(pC, ',');
		if(pC) pC++;
	}
}

void CBotNet::ConSetTargetAll(IConsole::IResult *pResult, void *pUserData) {
	CBotNet *p = (CBotNet *)pUserData;
	p->m_AllTarget = pResult->GetInteger(0) != 0;
}

void CBotNet::ConAttackSettings(IConsole::IResult *pResult, void *pUserData) {
	CBotNet *p = (CBotNet *)pUserData;
	p->m_AutoAim        = pResult->GetInteger(0) != 0;
	p->m_AutoFire       = pResult->GetInteger(1) != 0;
	p->m_AutoHook       = pResult->GetInteger(2) != 0;
	p->m_MoveEnabled    = pResult->GetInteger(3) != 0;
	p->m_StandEnabled   = pResult->GetInteger(4) != 0;
	p->m_RescueFrozen   = pResult->GetInteger(5) != 0;
	p->m_RescueAll      = pResult->GetInteger(6) != 0;
	p->m_KillOnFreeze   = pResult->GetInteger(7) != 0;
	p->m_AttackMain     = pResult->GetInteger(8) != 0;
	p->m_AutoHammer     = pResult->GetInteger(9) != 0;
}

void CBotNet::ConAttackEnable(IConsole::IResult *pResult, void *pUserData) {
	((CBotNet*)pUserData)->m_AttackEnabled = pResult->GetInteger(0) != 0;
}

void CBotNet::ConSetMain(IConsole::IResult *pResult, void *pUserData) {
	((CBotNet*)pUserData)->m_MainID = pResult->GetInteger(0);
}

void CBotNet::ConRandomAim(IConsole::IResult *pResult, void *pUserData) { 
	CBotNet *pSelf = (CBotNet *)pUserData;
	pSelf->m_RandomAim = pResult->GetInteger(0) != 0;
	if(pResult->NumArguments() > 1) pSelf->m_RandomAimInterval = pResult->GetInteger(1);
}

void CBotNet::ConCopyMoves(IConsole::IResult *pResult, void *pUserData) { 
	CBotNet *pSelf = (CBotNet *)pUserData;
	int ID = pResult->GetInteger(0);
	pSelf->m_CopyTargetID = ID;
	pSelf->m_CopyMoves = (ID >= 0);
	pSelf->m_LastTargetAttackTick = -1;
}

void CBotNet::ConClientDelay(IConsole::IResult *pResult, void *pUserData)
{
	((CBotNet*)pUserData)->m_ClientDelay = pResult->GetInteger(0);
}

void CBotNet::ConStandOnX(IConsole::IResult *pResult, void *pUserData)
{
	((CBotNet*)pUserData)->m_StandOnXOnly = pResult->GetInteger(0) != 0;
}