/**
 * Texas Owner's Title Policy Basic Premium Calculator
 * Effective Date: September 1, 2019
 * 
 * This module calculates the owner's title insurance premium based on the
 * Texas Department of Insurance approved rate schedule.
 * 
 * Features:
 *   - Exact decimal math (no intermediate rounding)
 *   - Configurable rounding mode for audit safety and compliance
 *   - Final premium formatted to 2 decimal places
 * 
 * Usage:
 *   const premium = calculateTexasTitlePremium(salesPrice);
 *   const premium = calculateTexasTitlePremium(salesPrice, { roundingMode: 'nearest_dollar' });
 * 
 * Configuration:
 *   roundingMode: 'none' (default) - Returns exact calculation to 2 decimal places
 *                 'nearest_dollar' - Rounds final result to nearest whole dollar
 */

(function(global) {
    'use strict';

    // =============================================================================
    // CONFIGURATION
    // =============================================================================

    const CONFIG = {
        // Default rounding mode: 'none' | 'nearest_dollar'
        // 'none' - Exact decimal calculation, formatted to 2 decimal places
        // 'nearest_dollar' - Round final premium to nearest whole dollar
        roundingMode: 'none',
        
        // Effective date of these rates
        effectiveDate: '2019-09-01'
    };

    // =============================================================================
    // RATE TABLE ($25,000 to $100,000 in $500 increments)
    // =============================================================================

    const RATE_TABLE = {
        25000: 328, 25500: 331, 26000: 335, 26500: 338, 27000: 340,
        27500: 343, 28000: 347, 28500: 350, 29000: 355, 29500: 358,
        30000: 361, 30500: 364, 31000: 368, 31500: 371, 32000: 374,
        32500: 378, 33000: 381, 33500: 385, 34000: 388, 34500: 392,
        35000: 395, 35500: 398, 36000: 401, 36500: 405, 37000: 408,
        37500: 412, 38000: 416, 38500: 419, 39000: 421, 39500: 425,
        40000: 428, 40500: 433, 41000: 435, 41500: 439, 42000: 442,
        42500: 446, 43000: 448, 43500: 452, 44000: 456, 44500: 459,
        45000: 463, 45500: 466, 46000: 469, 46500: 473, 47000: 475,
        47500: 478, 48000: 483, 48500: 487, 49000: 490, 49500: 493,
        50000: 496, 50500: 499, 51000: 501, 51500: 505, 52000: 510,
        52500: 514, 53000: 516, 53500: 520, 54000: 523, 54500: 526,
        55000: 529, 55500: 532, 56000: 537, 56500: 540, 57000: 543,
        57500: 547, 58000: 551, 58500: 553, 59000: 556, 59500: 560,
        60000: 564, 60500: 568, 61000: 571, 61500: 573, 62000: 577,
        62500: 581, 63000: 583, 63500: 587, 64000: 591, 64500: 594,
        65000: 597, 65500: 600, 66000: 604, 66500: 609, 67000: 612,
        67500: 613, 68000: 617, 68500: 621, 69000: 624, 69500: 627,
        70000: 631, 70500: 635, 71000: 639, 71500: 641, 72000: 644,
        72500: 648, 73000: 651, 73500: 654, 74000: 658, 74500: 662,
        75000: 666, 75500: 668, 76000: 671, 76500: 674, 77000: 678,
        77500: 681, 78000: 685, 78500: 689, 79000: 693, 79500: 694,
        80000: 698, 80500: 702, 81000: 706, 81500: 708, 82000: 711,
        82500: 716, 83000: 720, 83500: 722, 84000: 725, 84500: 729,
        85000: 732, 85500: 735, 86000: 738, 86500: 743, 87000: 747,
        87500: 749, 88000: 752, 88500: 756, 89000: 760, 89500: 762,
        90000: 765, 90500: 769, 91000: 773, 91500: 777, 92000: 779,
        92500: 783, 93000: 786, 93500: 790, 94000: 791, 94500: 796,
        95000: 801, 95500: 804, 96000: 805, 96500: 809, 97000: 813,
        97500: 817, 98000: 820, 98500: 824, 99000: 827, 99500: 830,
        100000: 832
    };

    // =============================================================================
    // TIER DEFINITIONS (amounts > $100,000)
    // Formula: premium = base + (excess × rate)
    // No intermediate rounding - exact decimal calculation
    // =============================================================================

    const TIERS = [
        { max: 1000000,    base: 832,    rate: 0.00527, floor: 100000 },    // Tier A: $100,001 – $1,000,000
        { max: 5000000,    base: 5575,   rate: 0.00433, floor: 1000000 },   // Tier B: $1,000,001 – $5,000,000
        { max: 15000000,   base: 22895,  rate: 0.00357, floor: 5000000 },   // Tier C: $5,000,001 – $15,000,000
        { max: 25000000,   base: 58595,  rate: 0.00254, floor: 15000000 },  // Tier D: $15,000,001 – $25,000,000
        { max: 50000000,   base: 83995,  rate: 0.00152, floor: 25000000 },  // Tier E: $25,000,001 – $50,000,000
        { max: 100000000,  base: 121995, rate: 0.00138, floor: 50000000 },  // Tier F: $50,000,001 – $100,000,000
        { max: Infinity,   base: 190995, rate: 0.00124, floor: 100000000 }  // Tier G: > $100,000,000
    ];

    const MIN_PREMIUM = 328;
    const MIN_POLICY_AMOUNT = 25000;

    // =============================================================================
    // HELPER FUNCTIONS
    // =============================================================================

    /**
     * Round up to the next $500 increment (for table lookup)
     * @param {number} amount - The policy amount
     * @returns {number} - The ceiling rounded up to nearest $500
     */
    function roundUpToNext500(amount) {
        return Math.ceil(amount / 500) * 500;
    }

    /**
     * Apply final rounding based on configuration
     * @param {number} value - The calculated premium
     * @param {string} roundingMode - 'none' or 'nearest_dollar'
     * @returns {number} - The final premium value
     */
    function applyFinalRounding(value, roundingMode) {
        switch (roundingMode) {
            case 'nearest_dollar':
                return Math.round(value);
            case 'none':
            default:
                // Return exact value to 2 decimal places (no rounding loss)
                return Math.round(value * 100) / 100;
        }
    }

    // =============================================================================
    // MAIN CALCULATOR
    // =============================================================================

    /**
     * Calculate the Texas Owner's Title Policy Basic Premium
     * Based on Texas DOI approved rates effective September 1, 2019
     * 
     * Uses exact decimal math with no intermediate rounding.
     * Final result formatted based on rounding mode configuration.
     * 
     * @param {number} salesPrice - The sales price / policy amount
     * @param {Object} options - Optional configuration
     * @param {string} options.roundingMode - 'none' (default) or 'nearest_dollar'
     * @returns {number} - The premium (0 if invalid input)
     */
    function calculateTexasTitlePremium(salesPrice, options) {
        // Merge options with defaults
        const opts = Object.assign({}, { roundingMode: CONFIG.roundingMode }, options || {});

        // Handle invalid input
        if (!salesPrice || isNaN(salesPrice) || salesPrice <= 0) {
            return 0;
        }

        const amount = parseFloat(salesPrice);
        let premium;

        // 1) Minimum premium (amount <= $25,000)
        if (amount <= MIN_POLICY_AMOUNT) {
            premium = MIN_PREMIUM;
            return applyFinalRounding(premium, opts.roundingMode);
        }

        // 2) Table lookup ($25,001 - $100,000)
        if (amount <= 100000) {
            const ceiling = roundUpToNext500(amount);
            const lookupKey = Math.min(ceiling, 100000);
            premium = RATE_TABLE[lookupKey] || MIN_PREMIUM;
            return applyFinalRounding(premium, opts.roundingMode);
        }

        // 3) Formula tiers (> $100,000)
        // premium = base + (excess × rate)
        // No intermediate rounding - exact decimal calculation
        for (const tier of TIERS) {
            if (amount <= tier.max) {
                const excess = amount - tier.floor;
                // Exact calculation: base + (excess × rate)
                premium = tier.base + (excess * tier.rate);
                return applyFinalRounding(premium, opts.roundingMode);
            }
        }

        // Fallback (should never reach here)
        return 0;
    }

    /**
     * Get the current configuration
     * @returns {Object} - Current config settings
     */
    function getTexasTitlePolicyConfig() {
        return Object.assign({}, CONFIG);
    }

    /**
     * Update the default configuration
     * @param {Object} newConfig - Configuration overrides
     */
    function setTexasTitlePolicyConfig(newConfig) {
        if (newConfig && typeof newConfig === 'object') {
            if (newConfig.roundingMode && ['none', 'nearest_dollar'].includes(newConfig.roundingMode)) {
                CONFIG.roundingMode = newConfig.roundingMode;
            }
        }
    }

    // =============================================================================
    // EXPOSE TO GLOBAL SCOPE
    // =============================================================================

    global.calculateTexasTitlePremium = calculateTexasTitlePremium;
    global.getTexasTitlePolicyConfig = getTexasTitlePolicyConfig;
    global.setTexasTitlePolicyConfig = setTexasTitlePolicyConfig;

})(typeof window !== 'undefined' ? window : this);
