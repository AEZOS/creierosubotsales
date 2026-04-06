import asyncio
import io
import os
import time
import logging
from datetime import datetime, timedelta
from jinja2 import Template
from fastapi import FastAPI, Request, Response, Form, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from database import db_session

app = FastAPI(title="Creierosu v9 Ultimate Dashboard")

from fastapi.responses import FileResponse
from config import DB_PATH, ASSETS_DIR

# Ensure assets directory exists for stock uploads
if not os.path.exists(ASSETS_DIR):
    os.makedirs(ASSETS_DIR)

# Custom route to ONLY allow authenticated admins to see product images
@app.get("/assets/{filename}")
async def get_secure_asset(request: Request, filename: str):
    if not is_authenticated(request):
        return JSONResponse(status_code=403, content={"error": "Access Denied"})
    
    file_path = os.path.join(ASSETS_DIR, filename)
    if not os.path.exists(file_path):
        return JSONResponse(status_code=404, content={"error": "File not found"})
    
    return FileResponse(file_path)

app.state.bot = None
DASHBOARD_PIN = os.getenv("DASHBOARD_PIN", "7777")

def is_authenticated(request: Request):
    return request.cookies.get("admin_session") == DASHBOARD_PIN

@app.get("/login", response_class=HTMLResponse)
async def login_page():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>SECURE ACCESS | CREIEROSU</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>body { background: #060609; color: #fff; display: flex; align-items: center; justify-content: center; height: 100vh; font-family: sans-serif; overflow: hidden; }</style>
    </head>
    <body class="p-6">
        <div class="w-full max-w-sm bg-white/5 border border-white/10 p-8 rounded-3xl text-center">
            <div class="w-16 h-16 bg-orange-600 rounded-full flex items-center justify-center mx-auto mb-6 shadow-2xl shadow-orange-500/20">
                <i class="fas fa-lock text-white text-xl"></i>
            </div>
            <h1 class="text-2xl font-black italic tracking-tighter mb-2">CREIEROSU <span class="text-orange-500">DECODER</span></h1>
            <p class="text-[10px] text-gray-600 font-bold uppercase tracking-widest mb-8 text-center">Enter Access PIN to bridge connection</p>
            <form action="/login" method="post" class="space-y-4">
                <input type="password" name="pin" placeholder="Enter PIN" class="w-full bg-black border border-white/10 rounded-xl px-4 py-3 text-center text-xl font-bold font-mono focus:border-orange-500 outline-none">
                <button type="submit" class="w-full bg-orange-600 py-3 rounded-xl font-black uppercase tracking-widest text-xs hover:bg-orange-500 transition-colors">Authorize Node</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.post("/login")
async def process_login(pin: str = Form(...)):
    if pin == DASHBOARD_PIN:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="admin_session", value=pin, max_age=86400 * 7, httponly=True)
        return response
    return RedirectResponse(url="/login?error=1", status_code=303)

TEMPLATES_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>⚡ CREIEROSU ADMIN COMMAND CENTER</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;900&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Inter', sans-serif; background-color: #060609; color: #fff; overscroll-behavior: none; }
        .glass { background: rgba(255, 255, 255, 0.03); backdrop-filter: blur(10px); border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 24px; padding: 1.5rem; }
        .nav-btn { transition: all 0.2s; border-radius: 12px; cursor: pointer; border: 1px solid transparent; }
        .nav-btn:hover { background: rgba(255,255,255,0.05); }
        .active-tab { background: #ea580c !important; color: #fff !important; box-shadow: 0 4px 15px rgba(234, 88, 12, 0.3); }
        .tab-content { display: none; }
        .tab-content.active { display: block; animation: fadeUp 0.4s ease; }
        @keyframes fadeUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .stat-card { transition: all 0.3s; cursor: pointer; }
        .stat-card:hover { transform: translateY(-5px); border-color: rgba(234, 88, 12, 0.4); background: rgba(234, 88, 12, 0.03); }
        .swal2-popup { border-radius: 32px !important; border: 1px solid rgba(255,255,255,0.1) !important; background: #0d0d12 !important; color: #fff !important; }
        .swal2-input, .swal2-select, .swal2-textarea { background: #000 !important; border: 1px solid #333 !important; color: #fff !important; border-radius: 12px !important; }
        .custom-scrollbar::-webkit-scrollbar { width: 4px; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: #ea580c; border-radius: 10px; }
    </style>
</head>
<body class="p-4 md:p-10">
    <div class="max-w-7xl mx-auto">
        <header class="flex flex-col md:flex-row justify-between items-center mb-8 md:mb-16 gap-6">
            <div class="flex items-center gap-4">
               <div class="w-10 h-10 md:w-12 md:h-12 bg-orange-600 rounded-full flex items-center justify-center shadow-lg shadow-orange-500/20">
                   <i class="fas fa-brain text-white"></i>
               </div>
               <h1 class="text-3xl md:text-4xl font-black italic tracking-tighter">CREIEROSU <span class="text-orange-500">ADMIN</span></h1>
            </div>
            <nav class="flex bg-white/5 p-2 rounded-2xl border border-white/5 gap-2 overflow-x-auto scrollbar-hide w-full md:w-auto">
                <button id="tab-overview" onclick="switchTab('overview')" class="nav-btn active-tab px-6 py-3 text-[10px] font-black uppercase tracking-widest whitespace-nowrap">Statistici</button>
                <button id="tab-store" onclick="switchTab('store')" class="nav-btn px-6 py-3 text-[10px] font-black uppercase tracking-widest whitespace-nowrap">Inventar</button>
                <button id="tab-users" onclick="switchTab('users')" class="nav-btn px-6 py-3 text-[10px] font-black uppercase tracking-widest whitespace-nowrap">Utilizatori</button>
                <button id="tab-wallets" onclick="switchTab('wallets')" class="nav-btn px-6 py-3 text-[10px] font-black uppercase tracking-widest whitespace-nowrap">Plăți</button>
            </nav>
            <div class="hidden md:flex items-center gap-4 bg-white/5 px-5 py-2.5 rounded-2xl border border-white/10">
                <div class="flex flex-col text-right">
                    <span class="text-[9px] font-black text-gray-500 uppercase tracking-widest">Protocol Time</span>
                    <span id="live-clock" class="text-xs font-mono font-black text-white">00:00:00</span>
                </div>
            </div>
        </header>

        <!-- Stats Overview -->
        <div id="overview" class="tab-content active transition-all">
            <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-12">
                <div class="glass flex flex-col justify-between border-l-4 border-l-orange-500 shadow-xl overflow-hidden relative group">
                    <div class="absolute -right-4 -top-4 text-orange-500/10 text-8xl group-hover:scale-110 transition-transform"><i class="fas fa-coins"></i></div>
                    <span class="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-6">Venituri Matrix</span>
                    <h2 id="stat-revenue" class="text-4xl font-black italic tracking-tighter">{{ revenue }} RON</h2>
                </div>
                <div onclick="showDetailedStats('completed')" class="glass stat-card flex flex-col justify-between border-l-4 border-l-green-500 shadow-xl relative group">
                    <div class="absolute -right-4 -top-4 text-green-500/10 text-8xl group-hover:scale-110 transition-transform"><i class="fas fa-check-circle"></i></div>
                    <span class="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-6">Tranzacții Finalizate</span>
                    <h2 id="stat-sales" class="text-4xl font-black italic tracking-tighter">{{ sales_count }}</h2>
                </div>
                <div onclick="showDetailedStats('pending')" class="glass stat-card flex flex-col justify-between border-l-4 border-l-yellow-500 shadow-xl relative group">
                    <div class="absolute -right-4 -top-4 text-yellow-500/10 text-8xl group-hover:scale-110 transition-transform"><i class="fas fa-hourglass-half"></i></div>
                    <span class="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-6">În Așteptare</span>
                    <h2 id="stat-pending" class="text-4xl font-black italic tracking-tighter">0</h2>
                </div>
                <div onclick="showDetailedStats('online')" class="glass stat-card flex flex-col justify-between border-l-4 border-l-blue-500 shadow-xl relative group">
                    <div class="absolute -right-4 -top-4 text-blue-500/10 text-8xl group-hover:scale-110 transition-transform"><i class="fas fa-user-circle"></i></div>
                    <span class="text-[10px] font-black text-gray-500 uppercase tracking-widest mb-6">Utilizatori Activi</span>
                    <h2 id="stat-online" class="text-4xl font-black italic tracking-tighter">0</h2>
                </div>
            </div>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-8">
                <!-- Activity Stream (Left 2/3) -->
                <div class="lg:col-span-2 glass !p-6 md:!p-10 border-white/10 shadow-2xl min-h-[600px]">
                    <div class="flex justify-between items-center mb-10">
                        <h3 class="text-2xl font-black italic uppercase tracking-tighter">Flux Activitate Direct</h3>
                        <div class="flex items-center gap-3">
                             <div class="w-2 h-2 bg-orange-500 rounded-full animate-pulse shadow-lg shadow-orange-500/50"></div>
                             <span class="text-[10px] font-black uppercase text-orange-500 tracking-widest">Sistem Activ</span>
                        </div>
                    </div>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-4" id="recent-ops">
                        <div class="text-gray-800 text-xs italic sm:col-span-2">Se conectează la rețea...</div>
                    </div>
                </div>

                <!-- Right Side: Protocol Summary -->
                <div class="space-y-6">
                    <div class="glass p-8 border-l-4 border-l-orange-500">
                        <h4 class="text-[10px] font-black uppercase tracking-widest text-gray-700 mb-6 flex items-center gap-2">
                             <i class="fas fa-server"></i>
                             Status Operațional Seif
                        </h4>
                        <div class="grid grid-cols-2 gap-8">
                             <div>
                                 <div id="stat-stock" class="text-3xl font-black italic text-white line-height-1">0</div>
                                 <div class="text-[9px] font-bold text-gray-500 uppercase mt-1 tracking-tighter">Produse în Stoc</div>
                             </div>
                             <div>
                                 <div id="stat-addresses" class="text-3xl font-black italic text-orange-500 line-height-1">0</div>
                                 <div class="text-[9px] font-bold text-gray-500 uppercase mt-1 tracking-tighter">Receptoare LTC</div>
                             </div>
                        </div>
                        <div class="mt-8 pt-8 border-t border-white/5 space-y-4">
                             <div class="flex items-center gap-4">
                                 <div class="w-8 h-8 rounded-lg bg-orange-500/10 flex items-center justify-center text-orange-400">
                                     <i class="fas fa-microchip text-[12px]"></i>
                                 </div>
                                 <div class="flex-1">
                                     <div class="flex justify-between text-[11px] mb-1 font-bold">
                                         <span class="text-gray-500 uppercase">Stare Sistem</span>
                                         <span class="text-white">STABIL</span>
                                     </div>
                                     <div class="w-full h-1 bg-white/5 rounded-full overflow-hidden">
                                         <div class="bg-orange-500 h-full w-[95%]"></div>
                                     </div>
                                 </div>
                             </div>
                        </div>
                    </div>

                    <div class="bg-white/[0.02] border border-white/5 p-6 rounded-2xl">
                        <h4 class="text-[10px] font-black uppercase tracking-widest text-gray-500 mb-4 italic">Acțiuni Rapide</h4>
                        <div class="grid grid-cols-2 gap-2">
                             <button onclick="switchTab('store')" class="bg-white/5 p-3 rounded-lg text-[9px] font-bold uppercase hover:bg-white/10 transition-colors">Gestiune Magazin</button>
                             <button onclick="switchTab('users')" class="bg-white/5 p-3 rounded-lg text-[9px] font-bold uppercase hover:bg-white/10 transition-colors">Vezi Utilizatori</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Wallets Content -->
        <div id="wallets" class="tab-content transition-all">
            <div class="flex justify-between items-center mb-10">
                <h3 class="text-2xl font-black italic uppercase tracking-tighter">Gestionare Receptoare Plăți</h3>
                <button onclick="addAddress()" class="bg-orange-600 px-6 py-3 rounded-xl text-xs font-black uppercase shadow-lg shadow-orange-500/20">Leagă Adresă Nouă</button>
            </div>
            <div id="address-list" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
        </div>

        <!-- Store Content -->
        <div id="store" class="tab-content transition-all">
            <div class="flex md:flex-row flex-col justify-between items-center mb-10 gap-4">
                <h3 class="text-2xl font-black italic uppercase tracking-tighter">Control Inventar & Categorii</h3>
                <div class="flex gap-4">
                    <button onclick="createCategory()" class="bg-white/5 border border-white/10 px-6 py-2.5 rounded-xl font-bold text-[10px] uppercase">+ Categorie</button>
                    <button onclick="createItem()" class="bg-orange-600 px-6 py-2.5 rounded-xl font-bold text-[10px] uppercase shadow-lg shadow-orange-500/20">+ Produs</button>
                </div>
            </div>
            <div id="category-menu" class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4 mb-10"></div>
            <div id="store-grid" class="space-y-12"></div>
        </div>

        <!-- Users Content -->
        <div id="users" class="tab-content transition-all">
            <h3 class="text-2xl font-black italic uppercase tracking-tighter mb-10">Baza Date Utilizatori</h3>
            <div id="user-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6"></div>
        </div>

    </div>

    <script>
        let activeCategoryId = null;
        window._cache = { categories: {}, items: {} };

        async function switchTab(id) {
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active-tab'));
            document.getElementById(id).classList.add('active');
            const tabBtn = document.getElementById('tab-' + id);
            if(tabBtn) tabBtn.classList.add('active-tab');

            if (id === 'store') loadStore();
            if (id === 'users') loadUsers();
            if (id === 'wallets') loadAddresses();
            if (id === 'overview') loadOps();
        }

        async function loadStore() {
            try {
                const res = await fetch('/api/inventory');
                if (res.status === 403) { window.location.reload(); return; }
                const data = await res.json();
                
                // Update Cache
                data.categories.forEach(c => window._cache.categories[c.id] = c.name);
                data.items.forEach(i => window._cache.items[i.id] = i.name);

                const menu = document.getElementById('category-menu');
                menu.innerHTML = data.categories.map(cat => {
                    const isActive = activeCategoryId === cat.id;
                    return `
                        <div onclick="selectCategory(${cat.id})" class="cursor-pointer group flex flex-col items-center justify-center p-6 glass border-white/5 hover:border-orange-500/40 transition-all ${isActive ? 'active-tab' : ''}">
                            <div class="text-4xl mb-2">${cat.name.split(' ')[0]}</div>
                            <div class="text-[9px] font-black uppercase tracking-widest text-center">${cat.name.split(' ').slice(1).join(' ') || cat.name}</div>
                        </div>
                    `;
                }).join('');

                const storeGrid = document.getElementById('store-grid');
                if (!activeCategoryId && data.categories.length > 0) {
                    activeCategoryId = data.categories[0].id;
                }
                
                if (activeCategoryId) {
                    const cat = data.categories.find(c => c.id === activeCategoryId);
                    const items = data.items.filter(i => i.category_id === activeCategoryId);
                    storeGrid.innerHTML = `
                        <div class="p-8 pb-4 flex justify-between items-center border-b border-white/5 mb-8">
                            <h3 class="text-3xl font-black italic uppercase italic text-orange-500">${cat.name}</h3>
                            <span class="text-[9px] font-black text-gray-600 uppercase tracking-widest">Sincronizat Matrix</span>
                        </div>
                        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                            ${items.map(item => `
                                <div class="p-8 glass flex flex-col group hover:border-orange-500/30 transition-all">
                                    <div class="flex justify-between items-start mb-6">
                                        <h4 class="text-xl font-black italic text-white">${item.name}</h4>
                                        <span class="bg-white/5 text-[9px] font-black text-orange-400 px-3 py-1.5 rounded-lg border border-orange-500/20">${item.stock_count} STOC</span>
                                    </div>
                                    <p class="text-[10px] text-gray-500 mb-8 italic uppercase font-bold tracking-tight">${item.description || 'Nicio descriere'}</p>
                                    <div class="mt-auto flex justify-between items-end">
                                        <div class="flex flex-col">
                                            <span class="text-orange-500 font-black text-2xl tracking-tighter">${item.price_ron} RON</span>
                                            <span class="text-gray-700 font-bold text-[9px] uppercase tracking-tighter">Pret Sistem</span>
                                        </div>
                                        <button onclick="addStock(${item.id})" class="bg-white/5 hover:bg-white text-white hover:text-black w-10 h-10 rounded-xl transition-all flex items-center justify-center">
                                            <i class="fas fa-plus"></i>
                                        </button>
                                    </div>
                                    <div class="mt-6 pt-4 border-t border-white/5 grid grid-cols-5 gap-2">
                                        ${item.stock.slice(0, 5).map(s => `
                                            <div class="relative group/s">
                                                <div class="w-full aspect-square bg-white/5 rounded-lg border border-white/10"></div>
                                                <button onclick="burnStock(${s.id})" class="absolute -top-1 -right-1 bg-red-600 w-4 h-4 rounded-full text-[8px] flex items-center justify-center opacity-0 group-hover/s:opacity-100 transition-opacity">
                                                    <i class="fas fa-times"></i>
                                                </button>
                                            </div>
                                        `).join('')}
                                    </div>
                                </div>
                            `).join('')}
                        </div>
                    `;
                }
            } catch (e) { console.error(e); }
        }

        function selectCategory(id) {
            activeCategoryId = id;
            loadStore();
        }

        async function loadUsers() {
            const res = await fetch('/api/users');
            if (res.status === 403) { window.location.reload(); return; }
            const data = await res.json();
            const grid = document.getElementById('user-grid');
            grid.innerHTML = data.users.map(u => {
                const pfp = u.profile_photo || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(u.username || u.telegram_id)}&backgroundColor=ea580c&textColor=fff`;
                const isOnline = u.last_activity_at && (new Date() - new Date(u.last_activity_at.replace(' ','T'))) < 900000;
                return `
                    <div onclick="showUserVault(${u.telegram_id})" class="cursor-pointer glass p-6 flex flex-col gap-6 group hover:border-orange-500/30 transition-all">
                        <div class="flex items-center gap-4">
                            <div class="relative">
                                <img src="${pfp}" class="w-14 h-14 rounded-2xl object-cover border border-white/10 shadow-lg group-hover:border-orange-500/40" onerror="this.src='https://api.dicebear.com/7.x/initials/svg?seed=${u.telegram_id}'">
                                <div class="absolute -bottom-1 -right-1 w-3.5 h-3.5 rounded-full border-2 border-black ${isOnline ? 'bg-green-500 animate-pulse' : 'bg-gray-800'}"></div>
                            </div>
                            <div class="flex-1 truncate">
                                <div class="font-black italic text-lg truncate">@${u.username || 'ANONIM'}</div>
                                <div class="text-[9px] text-gray-500 font-mono tracking-widest">${u.telegram_id}</div>
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-4">
                            <div class="bg-black/20 p-2.5 rounded-xl border border-white/5">
                                <div class="text-[8px] font-black uppercase text-gray-600 mb-0.5">Purchases</div>
                                <div class="text-xs font-black text-orange-500">${u.total_purchases || 0}</div>
                            </div>
                            <div class="bg-black/20 p-2.5 rounded-xl border border-white/5">
                                <div class="text-[8px] font-black uppercase text-gray-600 mb-0.5">Total Spent</div>
                                <div class="text-xs font-black text-white">${parseFloat(u.total_spent_ltc || 0).toFixed(4)} LTC</div>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');
        }

        async function loadAddresses() {
            const res = await fetch('/api/addresses');
            const data = await res.json();
            const list = document.getElementById('address-list');
            list.innerHTML = data.addresses.map(a => {
                const busy = a.in_use_by_sale_id != null;
                return `
                    <div class="glass p-6 border-white/5 relative group hover:border-orange-500/20">
                        <div class="flex justify-between items-start mb-6">
                            <div class="w-10 h-10 rounded-xl bg-orange-600/10 flex items-center justify-center text-orange-500"><i class="fas fa-wallet"></i></div>
                            <span class="text-[8px] font-black px-2 py-1 rounded-lg ${busy ? 'bg-yellow-500/20 text-yellow-500' : 'bg-green-500/20 text-green-500'}">${busy ? 'IN USE' : 'READY'}</span>
                        </div>
                        <code class="text-xs font-mono text-white block mb-6 transition-colors group-hover:text-orange-400 truncate">${a.crypto_address}</code>
                        <div class="flex justify-between items-center pt-4 border-t border-white/5">
                            <span class="text-[8px] text-gray-700 font-black">ID: ${a.id}</span>
                            <button onclick="deleteAddress(${a.id})" class="text-red-900 hover:text-red-500 text-[10px] font-black uppercase"><i class="fas fa-trash-alt"></i></button>
                        </div>
                    </div>
                `;
            }).join('');
        }

        async function loadOps() {
            try {
                const [actRes, statRes] = await Promise.all([
                    fetch('/api/activity'),
                    fetch('/api/stats')
                ]);
                if (actRes.status === 403) { window.location.reload(); return; }
                const actData = await actRes.json();
                const statData = await statRes.json();

                document.getElementById('stat-revenue').innerText = statData.revenue;
                document.getElementById('stat-sales').innerText = statData.sales_count;
                document.getElementById('stat-pending').innerText = statData.pending_count;
                document.getElementById('stat-online').innerText = statData.online_count;
                document.getElementById('stat-stock').innerText = statData.stock_count;
                document.getElementById('stat-addresses').innerText = statData.address_count;
                
                const ops = document.getElementById('recent-ops');
                ops.innerHTML = actData.activity.map(a => {
                    const pfp = a.profile_photo || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(a.username || a.telegram_id)}&backgroundColor=ea580c&textColor=fff`;
                    return `
                        <div onclick="showUserVault(${a.telegram_id})" class="flex items-center gap-4 p-4 glass hover:border-orange-500/20 transition-all cursor-pointer">
                            <img src="${pfp}" class="w-12 h-12 rounded-xl object-cover border border-white/10" onerror="this.src='https://api.dicebear.com/7.x/initials/svg?seed=${a.telegram_id}'">
                            <div class="flex-1 truncate">
                                <span class="text-xs font-black text-white">@${a.username || 'ANONIM'}</span>
                                <p class="text-[10px] text-orange-500 font-bold uppercase truncate tracking-tighter">${translateActivity(a.last_activity)}</p>
                            </div>
                            <span class="text-[9px] text-gray-700 font-mono">${a.last_activity_at.split(' ')[1]}</span>
                        </div>
                    `;
                }).join('');
            } catch(e) {}
        }

        function translateActivity(text) {
            if (!text) return '—';
            const t = text.toUpperCase();
            const raw = text.replace(/^Buton: /i, '').replace(/^Mesaj: /i, '');
            const cache = window._cache;

            if (/^shop_cat_/i.test(raw)) { 
                const id = raw.split('_')[2]; 
                const name = cache.categories[id];
                return name ? `🛍 ${name}` : `🔍 Categorie #${id}`; 
            }
            if (/^shop_item_/i.test(raw)) { 
                const id = raw.split('_')[2]; 
                const name = cache.items[id];
                return name ? `📦 ${name}` : `📦 Produs #${id}`; 
            }
            if (raw === 'menu_shop' || raw === 'nav_back_categories') return '🛍 DESCHIDE MAGAZIN';
            if (raw === 'menu_profile') return '👤 VERIFICĂ PROFIL';
            if (raw === 'menu_support') return '💬 CONTACT SUPORT';
            if (raw === 'menu_review' || t.includes('REVIEW')) return '⭐ LASĂ RECENZIE';
            if (t.includes('START')) return '🚀 A PORNIT BOTUL';
            if (t.includes('PAID')) return '✅ PLATA CONFIRMATĂ';
            if (t.includes('VERIFY_PAY')) return '💳 VERIFICĂ PLATA';
            if (t.includes('CANCEL')) return '❌ ANULEAZĂ TRANZACȚIA';
            if (t.includes('PURCHASE') || t.includes('BUY') || raw.startsWith('buy_item_')) return '🛒 INIȚIAZĂ CUMPĂRARE';
            return text.replace(/^Buton: /i, '').replace(/^Mesaj: /i, '');
        }

        async function showDetailedStats(type) {
            Swal.fire({ title: 'Interogare Matrix...', didOpen: () => Swal.showLoading() });
            const res = await fetch(`/api/detailed-stats/${type}`);
            const data = await res.json();
            
            let html = '<div class="text-left custom-scrollbar max-h-[60vh] overflow-y-auto">';
            
            if (type === 'completed') {
                html += '<h3 class="text-orange-500 font-black mb-6 uppercase tracking-widest text-xs">Ultimele 20 Tranzacții</h3>';
                data.data.forEach(s => {
                    html += `
                        <div class="p-4 mb-3 bg-white/5 border border-white/5 rounded-2xl flex justify-between items-center">
                            <div>
                                <div class="text-xs font-black">@${s.username || 'Anonim'}</div>
                                <div class="text-[10px] text-orange-400 font-bold mt-1">${s.item_name}</div>
                            </div>
                            <div class="text-right">
                                <div class="text-xs font-mono font-black">${s.amount_paid} LTC</div>
                                <div class="text-[8px] text-gray-600 mt-1 uppercase">${s.created_at}</div>
                            </div>
                        </div>
                    `;
                });
            } else if (type === 'pending') {
                html += '<h3 class="text-yellow-500 font-black mb-6 uppercase tracking-widest text-xs">Comenzi în Așteptare</h3>';
                if (data.data.length === 0) html += '<p class="text-gray-600 italic">Nicio comandă activă.</p>';
                data.data.forEach(s => {
                    html += `
                        <div class="p-4 mb-3 bg-white/5 border border-white/5 rounded-2xl flex justify-between items-center">
                            <div>
                                <div class="text-xs font-black">@${s.username || 'Anonim'}</div>
                                <div class="text-[10px] text-yellow-500 font-bold mt-1">${s.item_name}</div>
                            </div>
                            <div class="text-right">
                                <div class="text-xs font-mono font-black text-white">${s.amount_expected} LTC</div>
                                <div class="text-[8px] text-blue-400 uppercase font-black tracking-widest mt-1">${s.status}</div>
                            </div>
                        </div>
                    `;
                });
            } else if (type === 'online') {
                html += '<h3 class="text-blue-500 font-black mb-6 uppercase tracking-widest text-xs">Utilizatori Activi (15m)</h3>';
                data.data.forEach(u => {
                    html += `
                        <div class="p-4 mb-3 bg-white/5 border border-white/5 rounded-2xl flex items-center gap-4">
                            <img src="${u.profile_photo || 'https://api.dicebear.com/7.x/initials/svg?seed='+u.telegram_id}" class="w-10 h-10 rounded-xl">
                            <div class="flex-1">
                                <div class="text-xs font-black text-white">@${u.username || 'Anonim'}</div>
                                <div class="text-[9px] text-gray-500 italic mt-0.5">${u.last_activity}</div>
                            </div>
                            <div class="text-[9px] font-mono text-blue-400">${u.last_activity_at.split(' ')[1]}</div>
                        </div>
                    `;
                });
            }
            html += '</div>';
            
            Swal.fire({
                html: html,
                width: '600px',
                showConfirmButton: false,
                showCloseButton: true,
                background: '#0d0d12'
            });
        }

        async function showUserVault(tgId) {
            Swal.fire({ title: 'Interogare Seif...', didOpen: () => Swal.showLoading() });
            const res = await fetch(`/api/user-profile/${tgId}`);
            const data = await res.json();
            const pfp = data.user.profile_photo || `https://api.dicebear.com/7.x/initials/svg?seed=${tgId}&backgroundColor=ea580c`;
            
            Swal.fire({
                width: '800px',
                showConfirmButton: false,
                showCloseButton: true,
                html: `
                    <div class="text-left py-4">
                        <div class="flex items-center gap-6 mb-10 pb-8 border-b border-white/5">
                            <img src="${pfp}" class="w-20 h-20 rounded-[24px] object-cover border-4 border-orange-600/10">
                            <div>
                                <h2 class="text-3xl font-black italic tracking-tighter">@${data.user.username || 'Anonim'}</h2>
                                <p class="text-[10px] text-gray-600 font-black uppercase tracking-widest mt-1">Entity ID: ${tgId}</p>
                            </div>
                        </div>
                        <div class="grid grid-cols-2 gap-8">
                            <div class="space-y-4">
                                <h4 class="text-[10px] font-black text-orange-500 uppercase tracking-widest italic">Jurnal Recent</h4>
                                <div class="space-y-2 max-h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                                    ${data.activity.map(a => `
                                        <div class="p-3 bg-white/5 rounded-xl flex justify-between items-center text-[10px]">
                                            <span class="text-gray-400">${translateActivity(a.activity)}</span>
                                            <span class="font-mono text-gray-700">${a.created_at.split(' ')[1]}</span>
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                            <div class="space-y-4">
                                <h4 class="text-[10px] font-black text-green-500 uppercase tracking-widest italic">Istoric Achizitii</h4>
                                <div class="space-y-2 max-h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                                    ${data.sales.map(s => `
                                        <div class="p-3 bg-white/5 rounded-xl border-l-2 border-l-green-500">
                                            <div class="flex justify-between font-black text-[10px] mb-1">
                                                <span>${s.item_name}</span>
                                                <span class="text-green-500">${s.amount_paid} LTC</span>
                                            </div>
                                            <div class="text-[8px] text-gray-600 uppercase font-black tracking-widest">${s.created_at}</div>
                                        </div>
                                    `).join('') || '<p class="text-gray-800 italic text-[10px]">Nicio achizitie finalizata.</p>'}
                                </div>
                            </div>
                        </div>
                    </div>
                `
            });
        }

        async function createCategory() {
            const { value: name } = await Swal.fire({ 
                title: 'GENERATE NEW POOL', 
                input: 'text', 
                inputPlaceholder: 'Enter Emoji or Name',
                background: '#111', color: '#fff' 
            });
            if (name) { 
                const form = new FormData(); form.append('name', name);
                await fetch('/api/categories', { method: 'POST', body: form });
                loadStore();
            }
        }

        async function createItem() {
            const res = await fetch('/api/inventory');
            const data = await res.json();
            const { value: v } = await Swal.fire({
                title: 'INITIALIZE ASSET',
                html: `
                    <select id="sw-cat" class="swal2-input">${data.categories.map(c => `<option value="${c.id}">${c.name}</option>`).join('')}</select>
                    <input id="sw-name" class="swal2-input" placeholder="Display Name">
                    <input id="sw-desc" class="swal2-input" placeholder="Protocol Description">
                    <input id="sw-price" type="number" class="swal2-input" placeholder="Value (RON)">`,
                preConfirm: () => [document.getElementById('sw-cat').value, document.getElementById('sw-name').value, document.getElementById('sw-desc').value, document.getElementById('sw-price').value]
            });
            if (v) {
                const f = new FormData(); f.append('category_id', v[0]); f.append('name', v[1]); f.append('description', v[2]); f.append('price_ron', v[3]);
                await fetch('/api/items', { method: 'POST', body: f });
                loadStore();
            }
        }

        async function addStock(itemId) {
            const { value: v } = await Swal.fire({
                title: 'UPLOAD LIQUIDITY',
                html: '<input id="sw-f" type="file" class="swal2-input"><textarea id="sw-c" class="swal2-input" placeholder="Caption"></textarea>',
                preConfirm: () => ({ file: document.getElementById('sw-f').files[0], caption: document.getElementById('sw-c').value })
            });
            if (v) {
                const f = new FormData(); f.append('item_id', itemId); f.append('caption', v.caption);
                if (v.file) f.append('file', v.file);
                await fetch('/api/stock', { method: 'POST', body: f });
                loadStore();
            }
        }

        async function burnStock(id) {
            await fetch(`/api/stock/${id}`, { method: 'DELETE' });
            loadStore();
        }

        async function addAddress() {
            const { value: addr } = await Swal.fire({ title: 'LINK LTC WALLET', input: 'text', background: '#111', color: '#fff' });
            if (addr) {
                const f = new FormData(); f.append('address', addr);
                await fetch('/api/addresses', { method: 'POST', body: f });
                loadAddresses();
            }
        }

        async function deleteAddress(id) {
            await fetch(`/api/addresses/${id}`, { method: 'DELETE' });
            loadAddresses();
        }

        function updateClock() {
            document.getElementById('live-clock').innerText = new Date().toLocaleTimeString('en-GB');
        }
        setInterval(updateClock, 1000);
        setInterval(loadOps, 3000);
        updateClock();
        loadOps();
        loadStore();
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not is_authenticated(request): 
        return RedirectResponse(url="/login")
    
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) as count FROM users")
            user_count = (await cursor.fetchone())['count']
            await cursor.execute("SELECT COUNT(*) as count, SUM(amount_paid) as revenue FROM sales WHERE status IN ('paid', 'completed')")
            row = await cursor.fetchone()
        
    return Template(TEMPLATES_HTML).render(
        user_count=user_count, 
        sales_count=row['count'], 
        revenue=round(float(row['revenue'] or 0), 2)
    )

@app.get("/api/inventory")
async def get_inventory(request: Request):
    if not is_authenticated(request): return JSONResponse(status_code=403, content={"error": "Unauthorized"})
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT * FROM categories")
            cats = await cursor.fetchall()
            await cursor.execute("SELECT * FROM items")
            items = await cursor.fetchall()
            await cursor.execute("SELECT * FROM item_images WHERE is_sold = FALSE")
            stock = await cursor.fetchall()
    for item in items:
        item["stock"] = [s for s in stock if s["item_id"] == item["id"]]
        item["stock_count"] = len(item["stock"])
    return {"categories": cats, "items": items}

@app.get("/api/stats")
async def api_stats(request: Request):
    if not is_authenticated(request): return JSONResponse(status_code=403, content={"error": "Unauthorized"})
    fifteen_mins_ago = datetime.now() - timedelta(minutes=15)
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) as count, SUM(amount_paid) as revenue FROM sales WHERE status IN ('paid', 'completed')")
            sales = await cursor.fetchone()
            await cursor.execute("SELECT COUNT(*) as count FROM sales WHERE status IN ('pending', 'confirming')")
            pending = await cursor.fetchone()
            await cursor.execute("SELECT COUNT(*) as count FROM users WHERE last_activity_at > %s", (fifteen_mins_ago,))
            online = await cursor.fetchone()
            await cursor.execute("SELECT COUNT(*) as count FROM item_images WHERE is_sold = FALSE")
            stock = await cursor.fetchone()
            await cursor.execute("SELECT COUNT(*) as count FROM addresses")
            addresses = await cursor.fetchone()
 
    return {
        "revenue": f"{round(float(sales['revenue'] or 0), 2)} RON" if sales['revenue'] else "0 RON",
        "sales_count": sales['count'],
        "pending_count": pending['count'],
        "online_count": online['count'],
        "stock_count": stock['count'],
        "address_count": addresses['count']
    }

@app.get("/api/detailed-stats/{type}")
async def get_detailed_stats(request: Request, type: str):
    if not is_authenticated(request): return JSONResponse(status_code=403, content={"error": "Unauthorized"})
    async with db_session() as db:
        async with db.cursor() as cursor:
            if type == "completed":
                await cursor.execute("""
                    SELECT s.id, u.username, i.name as item_name, s.amount_paid, s.created_at
                    FROM sales s JOIN users u ON s.user_id = u.id JOIN items i ON s.item_id = i.id
                    WHERE s.status IN ('paid', 'completed') ORDER BY s.id DESC LIMIT 20
                """)
                rows = await cursor.fetchall()
            elif type == "pending":
                await cursor.execute("""
                    SELECT s.id, u.username, i.name as item_name, s.amount_expected, s.created_at, s.status
                    FROM sales s JOIN users u ON s.user_id = u.id JOIN items i ON s.item_id = i.id
                    WHERE s.status IN ('pending', 'confirming') ORDER BY s.id DESC
                """)
                rows = await cursor.fetchall()
            elif type == "online":
                t = datetime.now() - timedelta(minutes=15)
                await cursor.execute("SELECT telegram_id, username, last_activity, last_activity_at, profile_photo FROM users WHERE last_activity_at > %s ORDER BY last_activity_at DESC", (t,))
                rows = await cursor.fetchall()
            else: return {"data": []}
            return {"data": [dict(r) for r in rows]}

@app.get("/api/activity")
async def get_activity(request: Request):
    if not is_authenticated(request): return JSONResponse(status_code=403, content={"error": "Unauthorized"})
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT telegram_id, username, last_activity, last_activity_at, profile_photo FROM users WHERE last_activity IS NOT NULL ORDER BY last_activity_at DESC LIMIT 50")
            act = await cursor.fetchall()
    return {"activity": act}

@app.get("/api/user-profile/{tg_id}")
async def get_user_profile(request: Request, tg_id: int):
    if not is_authenticated(request): return JSONResponse(status_code=403, content={"error": "Unauthorized"})
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT * FROM users WHERE telegram_id = %s", (tg_id,))
            user = await cursor.fetchone()
            if not user: return JSONResponse(status_code=404, content={"error": "Not Found"})
            await cursor.execute("SELECT activity, created_at FROM user_activity_logs WHERE telegram_id = %s ORDER BY id DESC LIMIT 50", (tg_id,))
            activity = await cursor.fetchall()
            await cursor.execute("SELECT s.*, i.name as item_name FROM sales s JOIN items i ON s.item_id = i.id WHERE s.user_id = %s ORDER BY s.created_at DESC", (user['id'],))
            sales = await cursor.fetchall()
    return {"user": user, "activity": activity, "sales": sales}

@app.get("/api/users")
async def get_users(request: Request):
    if not is_authenticated(request): return JSONResponse(status_code=403, content={"error": "Unauthorized"})
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("""
                SELECT u.*, COUNT(s.id) as total_purchases, SUM(CASE WHEN s.status IN ('paid','completed') THEN s.amount_paid ELSE 0 END) as total_spent_ltc
                FROM users u LEFT JOIN sales s ON s.user_id = u.id GROUP BY u.id ORDER BY u.joined_at DESC LIMIT 100
            """)
            users = await cursor.fetchall()
    return {"users": users}

@app.get("/api/addresses")
async def get_addresses(request: Request):
    if not is_authenticated(request): return JSONResponse(status_code=403, content={"error": "Unauthorized"})
    async with db_session() as db:
        async with db.cursor() as cursor:
            await cursor.execute("SELECT * FROM addresses")
            addr = await cursor.fetchall()
    return {"addresses": addr}

@app.post("/api/categories")
async def add_category(name: str = Form(...)):
    async with db_session() as db:
        await db.execute("INSERT INTO categories (name) VALUES (%s)", (name,))
        await db.commit()
    return {"status": "ok"}

@app.post("/api/items")
async def add_item(category_id: int = Form(...), name: str = Form(...), description: str = Form(...), price_ron: float = Form(...)):
    async with db_session() as db:
        await db.execute("INSERT INTO items (category_id, name, description, price_ron, price_ltc) VALUES (%s, %s, %s, %s, %s)", (category_id, name, description, price_ron, price_ron/300.0))
        await db.commit()
    return {"status": "ok"}

@app.post("/api/stock")
async def add_stock_api(item_id: int = Form(...), caption: str = Form(None), file: UploadFile = File(None)):
    content = ""
    if file and file.filename:
        fname = f"stock_{item_id}_{int(time.time())}_{file.filename}"
        fpath = os.path.join(ASSETS_DIR, fname)
        with open(fpath, "wb") as f: f.write(await file.read())
        content = fpath
    async with db_session() as db:
        await db.execute("INSERT INTO item_images (item_id, image_url, caption) VALUES (%s, %s, %s)", (item_id, content, caption))
        await db.commit()
    return {"status": "ok"}

@app.delete("/api/stock/{id}")
async def delete_stock(id: int):
    async with db_session() as db:
        await db.execute("DELETE FROM item_images WHERE id = %s", (id,))
        await db.commit()
    return {"status": "ok"}

@app.delete("/api/addresses/{id}")
async def delete_address(id: int):
    async with db_session() as db:
        await db.execute("DELETE FROM addresses WHERE id = %s", (id,))
        await db.commit()
    return {"status": "ok"}

@app.post("/api/addresses")
async def add_address(address: str = Form(...)):
    async with db_session() as db:
        await db.execute("INSERT INTO addresses (crypto_address) VALUES (%s) ON CONFLICT DO NOTHING", (address,))
        await db.commit()
    return {"status": "ok"}

@app.delete("/api/items/{id}")
async def delete_item_api(request: Request, id: int):
    if not is_authenticated(request): return JSONResponse(status_code=403, content={"error": "Unauthorized"})
    async with db_session() as db:
        async with db.cursor() as cursor:
            # Check if is_primary exists or just delete
            try:
                await cursor.execute("SELECT id FROM items WHERE id = %s", (id,))
                it = await cursor.fetchone()
                if not it: return JSONResponse(status_code=404, content={"error": "Not Found"})
                await db.execute("DELETE FROM items WHERE id = %s", (id,))
                await db.commit()
            except Exception as e:
                return JSONResponse(status_code=500, content={"error": str(e)})
    return {"status": "ok"}

@app.get("/api/media/proxy/{file_id:path}")
async def proxy_media(request: Request, file_id: str):
    if not is_authenticated(request): return Response(status_code=403)
    bot = getattr(app.state, "bot", None)
    if not bot: return Response(content=file_id.encode(), media_type="text/plain")
    try:
        if len(file_id) < 15 or file_id.startswith("http") or "/" in file_id: 
            return Response(content=file_id.encode(), media_type="text/plain")
        f = await bot.get_file(file_id)
        d = io.BytesIO()
        await bot.download_file(f.file_path, d)
        return Response(content=d.getvalue(), media_type="image/jpeg")
    except: return Response(content=file_id.encode(), media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)
