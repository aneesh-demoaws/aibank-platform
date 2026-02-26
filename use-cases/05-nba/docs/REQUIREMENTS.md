# Next Best Action (NBA) — Requirements

## Overview
Decision intelligence platform combining ML propensity scoring (Amazon Personalize), graph relationship intelligence (Amazon Neptune), and agentic AI reasoning (AgentCore + Claude). Two-speed architecture: fast 50ms ML path + rich 2-5s agent reasoning path.

Inspired by CommBank's Customer Engagement Engine (35M+ decisions/day), adapted for GCC FSI.

## Prerequisites
| Dependency | Status | Why Required |
|-----------|--------|-------------|
| Foundation (Aurora, Cognito, SES) | ✅ Done | Customer data, auth, email |
| Alma FAQ + Onboarding | ✅ Done | Customer acquisition pipeline |
| Synthetic Data (82 customers, 33K txns) | ✅ Done | Training data for Personalize |
| **KYC Processing** | ❌ **NEXT** | **Employer, salary, address data needed for Neptune graph + Personalize user metadata. Without KYC, NBA runs on incomplete data.** |

## Key Requirements
| ID | Requirement | Priority | Engine |
|----|-------------|----------|--------|
| NB-01 | Customer 360 profile aggregation (financial health, savings rate, engagement) | P0 | Aurora |
| NB-02 | ML propensity scoring — Next-Best-Action recipe with 15 banking actions | P0 | Personalize |
| NB-03 | Graph relationship intelligence — household, peer network, community trends | P0 | Neptune |
| NB-04 | Agentic AI reasoning with explainable recommendations and talking points | P0 | AgentCore |
| NB-05 | Product eligibility checking (salary thresholds, account age, KYC status) | P0 | Lambda |
| NB-06 | Contact policy / frequency caps (max 3/week, 7-day same-action gap) | P0 | Aurora |
| NB-07 | Two-speed API: fast path (~50ms) + rich path (2-5s) | P0 | Lambda |
| NB-08 | Real-time feedback loop (accept/decline → Personalize + Neptune update) | P1 | EventBridge |
| NB-09 | Daily batch pipeline (360 metrics, graph sync, Personalize export) | P1 | EventBridge |
| NB-10 | Customer "For You" feed in app | P1 | Frontend |
| NB-11 | RM Advisor console with full reasoning display | P1 | Frontend |
| NB-12 | Marketing batch campaign console | P2 | Frontend |
| NB-13 | GCC-native: Islamic finance rules, multi-currency, Ramadan patterns | P0 | Agent prompt |
| NB-14 | Audit trail — every recommendation logged with reasoning and outcome | P0 | Aurora |

## Banking Actions (15)
| ID | Action | Type | Value |
|----|--------|------|-------|
| ACT001 | Upgrade to Premium Account | cross_sell | 500 |
| ACT002 | Apply for Personal Loan | cross_sell | 1000 |
| ACT003 | Open Savings Account | cross_sell | 200 |
| ACT004 | Complete KYC Verification | nudge | 100 |
| ACT005 | Set Up Auto-Save Goal | engagement | 150 |
| ACT006 | Enable Budget Alerts | advice | 50 |
| ACT007 | Refer a Friend | engagement | 300 |
| ACT008 | Apply for Credit Card | cross_sell | 800 |
| ACT009 | Open Business Account | cross_sell | 400 |
| ACT010 | Spending Insight Review | advice | 30 |
| ACT011 | App Feature Discovery | nudge | 20 |
| ACT012 | Investment Fund | cross_sell | 600 |
| ACT013 | Salary Advance | advice | 200 |
| ACT014 | Financial Health Check | engagement | 100 |
| ACT015 | Insurance Product | cross_sell | 700 |

## Three Audiences
| Audience | Interface | Speed | Content |
|----------|-----------|-------|---------|
| Customer | "For You" feed in app | Fast (50ms) | Action cards with friendly copy |
| RM (Relationship Manager) | Advisor console | Rich (2-5s) | Full reasoning, talking points, objection handling |
| Marketing | Campaign console | Batch | Segment targeting, personalized messages |

## Status: Blocked on KYC
